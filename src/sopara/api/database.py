from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from sopara.adapters.database.engine import Database
from sopara.adapters.database.repositories import CommandReceipt, CommandRepository
from sopara.adapters.database.schema import (
    data_source,
    dataset_manifest,
    decision,
    domain_event,
    entitlement_snapshot,
    experiment,
    export_manifest,
    incident,
    outbox_event,
    reconciliation,
    replay_run,
    trading_session,
)
from sopara.api.ports import (
    ControlPlaneUnavailableError,
    InvalidCommandError,
    ReadinessRow,
    ReadPage,
    ResourceNotFoundError,
    StaleResourceVersionError,
    StreamEvent,
    StreamWindow,
)

COLLECTIONS: dict[str, tuple[sa.Table, sa.Column[Any]]] = {
    "datasets": (dataset_manifest, dataset_manifest.c.manifest_id),
    "experiments": (experiment, experiment.c.record_id),
    "replays": (replay_run, replay_run.c.run_id),
    "sessions": (trading_session, trading_session.c.session_id),
    "decisions": (decision, decision.c.record_id),
    "incidents": (incident, incident.c.record_id),
    "reconciliations": (reconciliation, reconciliation.c.record_id),
    "reports": (export_manifest, export_manifest.c.record_id),
}

COMMAND_TARGETS = {
    "CREATE_EXPERIMENT": "DATASET",
    "START_REPLAY": "EXPERIMENT",
    "RESUME_REPLAY": "REPLAY_RUN",
    "RECONCILE_REPLAY": "REPLAY_RUN",
    "REQUEST_REPORT": "REPLAY_RUN",
    "REQUEST_EXPORT": "REPLAY_RUN",
    "HALT_SIMULATION": "SYSTEM",
}


def _public_row(row: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in row.items() if key not in {"owner_hash"}}


class PostgresControlPlaneRepository:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._commands = CommandRepository()

    async def ping(self) -> None:
        try:
            async with self._database.transaction() as connection:
                await connection.execute(sa.text("SELECT 1"))
        except SQLAlchemyError as error:
            raise ControlPlaneUnavailableError("database is unavailable") from error

    async def readiness(self) -> ReadinessRow:
        try:
            async with self._database.transaction() as connection:
                stream_sequence = int(
                    (await connection.scalar(sa.select(sa.func.max(outbox_event.c.outbox_seq))))
                    or 0
                )
                last_domain_event_at = await connection.scalar(
                    sa.select(sa.func.max(domain_event.c.recorded_at))
                )
                last_ingest_at = await connection.scalar(
                    sa.select(sa.func.max(dataset_manifest.c.committed_at))
                )
                latest_evidence = await connection.scalar(
                    sa.select(dataset_manifest.c.evidence_class)
                    .order_by(dataset_manifest.c.committed_at.desc())
                    .limit(1)
                )
                incident_count = int(
                    await connection.scalar(sa.select(sa.func.count(incident.c.record_id))) or 0
                )
                active_window = await connection.scalar(
                    sa.select(trading_session.c.state)
                    .order_by(trading_session.c.trade_date.desc())
                    .limit(1)
                )
            return ReadinessRow(
                revision=stream_sequence,
                stream_sequence=stream_sequence,
                generated_at=datetime.now(UTC),
                last_domain_event_at=cast("datetime | None", last_domain_event_at),
                last_ingest_at=cast("datetime | None", last_ingest_at),
                evidence_class=str(latest_evidence or "NO_EXECUTION_EVIDENCE"),
                source_health="READY" if last_ingest_at is not None else "NO_DATASET",
                window_state=str(active_window or "NO_SESSION"),
                incident_count=incident_count,
            )
        except SQLAlchemyError as error:
            raise ControlPlaneUnavailableError("database is unavailable") from error

    async def page(self, collection: str, *, after: str | None, limit: int) -> ReadPage:
        if collection == "entitlements":
            table = entitlement_snapshot
            key = entitlement_snapshot.c.snapshot_id
            statement = sa.select(
                entitlement_snapshot.c.snapshot_id,
                entitlement_snapshot.c.source_id,
                data_source.c.name.label("source_name"),
                data_source.c.provider,
                entitlement_snapshot.c.effective_from,
                entitlement_snapshot.c.effective_through,
                entitlement_snapshot.c.permitted_uses,
                entitlement_snapshot.c.policy_hash,
            ).join(data_source, entitlement_snapshot.c.source_id == data_source.c.source_id)
        else:
            try:
                table, key = COLLECTIONS[collection]
            except KeyError as error:
                raise ValueError(f"unknown collection: {collection}") from error
            statement = sa.select(table)
        if after is not None:
            try:
                after_id = UUID(after)
            except ValueError as error:
                raise ValueError("cursor resource identifier is invalid") from error
            statement = statement.where(key > after_id)
        statement = statement.order_by(key).limit(limit + 1)
        try:
            async with self._database.transaction() as connection:
                rows = list((await connection.execute(statement)).mappings())
                revision = int(
                    (await connection.scalar(sa.select(sa.func.max(outbox_event.c.outbox_seq))))
                    or 0
                )
        except SQLAlchemyError as error:
            raise ControlPlaneUnavailableError("database is unavailable") from error
        has_more = len(rows) > limit
        visible = rows[:limit]
        next_value = str(visible[-1][key.name]) if has_more else None
        return ReadPage(
            items=tuple(_public_row(cast("Mapping[str, object]", row)) for row in visible),
            next_value=next_value,
            snapshot_revision=revision,
        )

    async def detail(self, collection: str, resource_id: UUID) -> Mapping[str, object]:
        try:
            table, key = COLLECTIONS[collection]
        except KeyError as error:
            raise ValueError(f"unknown collection: {collection}") from error
        try:
            async with self._database.transaction() as connection:
                row = (
                    (await connection.execute(sa.select(table).where(key == resource_id)))
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError as error:
            raise ControlPlaneUnavailableError("database is unavailable") from error
        if row is None:
            raise ResourceNotFoundError(f"{collection} resource was not found")
        return _public_row(cast("Mapping[str, object]", row))

    async def submit_command(
        self,
        *,
        command_id: UUID,
        idempotency_key: str,
        payload_hash: str,
        command_type: str,
        target_type: str,
        target_id: UUID,
        expected_version: int,
        actor_email_hash: str,
    ) -> CommandReceipt:
        if COMMAND_TARGETS.get(command_type) != target_type:
            raise InvalidCommandError("command type and target type are incompatible")
        try:
            async with self._database.transaction() as connection:
                current_version = await self._locked_target_version(
                    connection, target_type=target_type, target_id=target_id
                )
                if current_version != expected_version:
                    raise StaleResourceVersionError(current_version)
                return await self._commands.receive(
                    connection,
                    command_id=command_id,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type=command_type,
                    target_type=target_type,
                    target_id=target_id,
                    expected_version=expected_version,
                    actor_email_hash=actor_email_hash,
                )
        except SQLAlchemyError as error:
            raise ControlPlaneUnavailableError("database is unavailable") from error

    async def _locked_target_version(
        self, connection: Any, *, target_type: str, target_id: UUID
    ) -> int:
        if target_type == "SYSTEM":
            return 0
        if target_type == "DATASET":
            statement = sa.select(dataset_manifest.c.generation).where(
                dataset_manifest.c.manifest_id == target_id
            )
        elif target_type == "EXPERIMENT":
            statement = sa.select(sa.literal(1)).where(experiment.c.record_id == target_id)
        elif target_type == "REPLAY_RUN":
            statement = sa.select(replay_run.c.row_version).where(replay_run.c.run_id == target_id)
        else:
            raise InvalidCommandError("target type is unsupported")
        current = await connection.scalar(statement.with_for_update())
        if current is None:
            raise ResourceNotFoundError("command target was not found")
        return int(current)

    async def stream_window(self, *, after: int, limit: int) -> StreamWindow:
        try:
            async with self._database.transaction() as connection:
                minimum, maximum = (
                    await connection.execute(
                        sa.select(
                            sa.func.min(outbox_event.c.outbox_seq),
                            sa.func.max(outbox_event.c.outbox_seq),
                        )
                    )
                ).one()
                rows = (
                    await connection.execute(
                        sa.select(outbox_event)
                        .where(outbox_event.c.outbox_seq > after)
                        .order_by(outbox_event.c.outbox_seq)
                        .limit(limit)
                    )
                ).mappings()
                events = tuple(
                    StreamEvent(
                        sequence=int(row["outbox_seq"]),
                        topic=str(row["topic"]),
                        payload=cast("Mapping[str, object]", row["payload"]),
                        committed_at=cast("datetime", row["committed_at"]),
                    )
                    for row in rows
                )
        except SQLAlchemyError as error:
            raise ControlPlaneUnavailableError("database is unavailable") from error
        return StreamWindow(
            minimum_sequence=int(minimum) if minimum is not None else None,
            maximum_sequence=int(maximum) if maximum is not None else None,
            events=events,
        )
