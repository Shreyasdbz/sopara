from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from sopara.adapters.database.errors import (
    IdempotencyConflictError,
    OptimisticConcurrencyError,
    StaleLeaseError,
)
from sopara.adapters.database.schema import (
    aggregate_projection,
    audit_event,
    command_inbox,
    dataset_manifest,
    domain_event,
    market_object,
    outbox_event,
    window_lease,
)
from sopara.application.ports import MarketObjectExpectation, StoredObject
from sopara.domain.canonical import canonical_sha256
from sopara.domain.events import DomainEvent
from sopara.domain.evidence import EvidenceClass


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    command_id: UUID
    state: str
    duplicate: bool


@dataclass(frozen=True, slots=True)
class Lease:
    lease_key: str
    lease_epoch: int
    holder_execution: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Projection:
    aggregate_type: str
    aggregate_id: UUID
    state: str
    row_version: int
    last_event_id: UUID


class CommandRepository:
    async def receive(
        self,
        connection: AsyncConnection,
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
        scope = {
            "actor_email_hash": actor_email_hash,
            "command_type": command_type,
            "target_id": target_id,
            "idempotency_key": idempotency_key,
        }
        statement = (
            insert(command_inbox)
            .values(
                command_id=command_id,
                payload_hash=payload_hash,
                target_type=target_type,
                expected_version=expected_version,
                **scope,
            )
            .on_conflict_do_nothing(constraint="uq_command_idempotency_scope")
            .returning(command_inbox.c.command_id, command_inbox.c.state)
        )
        inserted = (await connection.execute(statement)).one_or_none()
        if inserted is not None:
            return CommandReceipt(inserted.command_id, inserted.state, duplicate=False)

        existing = (
            await connection.execute(
                sa.select(
                    command_inbox.c.command_id,
                    command_inbox.c.state,
                    command_inbox.c.payload_hash,
                ).where(*(command_inbox.c[key] == value for key, value in scope.items()))
            )
        ).one()
        if existing.payload_hash != payload_hash:
            raise IdempotencyConflictError("idempotency key was reused with a different payload")
        return CommandReceipt(existing.command_id, existing.state, duplicate=True)


class LeaseRepository:
    async def acquire(
        self,
        connection: AsyncConnection,
        *,
        lease_key: str,
        holder_execution: str,
        ttl_seconds: int,
    ) -> Lease | None:
        if ttl_seconds <= 0:
            raise ValueError("lease TTL must be positive")
        statement = sa.text(
            """
            INSERT INTO sopara.window_lease (
                lease_key, lease_epoch, holder_execution, acquired_at, expires_at,
                last_heartbeat_at
            )
            VALUES (:lease_key, 1, :holder, clock_timestamp(),
                    clock_timestamp() + make_interval(secs => :ttl), clock_timestamp())
            ON CONFLICT (lease_key) DO UPDATE
            SET lease_epoch = sopara.window_lease.lease_epoch + 1,
                holder_execution = EXCLUDED.holder_execution,
                acquired_at = clock_timestamp(),
                expires_at = clock_timestamp() + make_interval(secs => :ttl),
                last_heartbeat_at = clock_timestamp()
            WHERE sopara.window_lease.expires_at <= clock_timestamp()
               OR sopara.window_lease.holder_execution = EXCLUDED.holder_execution
            RETURNING lease_key, lease_epoch, holder_execution, expires_at
            """
        )
        row = (
            await connection.execute(
                statement, {"lease_key": lease_key, "holder": holder_execution, "ttl": ttl_seconds}
            )
        ).one_or_none()
        if row is None:
            return None
        return Lease(row.lease_key, row.lease_epoch, row.holder_execution, row.expires_at)

    async def heartbeat(
        self,
        connection: AsyncConnection,
        *,
        lease_key: str,
        holder_execution: str,
        lease_epoch: int,
        ttl_seconds: int,
    ) -> Lease:
        statement = sa.text(
            """
            UPDATE sopara.window_lease
            SET last_heartbeat_at = clock_timestamp(),
                expires_at = clock_timestamp() + make_interval(secs => :ttl)
            WHERE lease_key = :lease_key
              AND holder_execution = :holder
              AND lease_epoch = :epoch
              AND expires_at > clock_timestamp()
            RETURNING lease_key, lease_epoch, holder_execution, expires_at
            """
        )
        row = (
            await connection.execute(
                statement,
                {
                    "lease_key": lease_key,
                    "holder": holder_execution,
                    "epoch": lease_epoch,
                    "ttl": ttl_seconds,
                },
            )
        ).one_or_none()
        if row is None:
            raise StaleLeaseError("lease heartbeat rejected for stale holder or epoch")
        return Lease(row.lease_key, row.lease_epoch, row.holder_execution, row.expires_at)


class EventRepository:
    async def append(
        self,
        connection: AsyncConnection,
        *,
        event: DomainEvent,
        aggregate_id: UUID,
        event_id: UUID,
        correlation_id: UUID,
        causation_id: UUID,
        actor_type: str,
        runtime_manifest_hash: str,
        projection_state: str,
        expected_version: int,
        topic: str,
        lease_key: str | None = None,
        lease_epoch: int | None = None,
    ) -> Projection:
        if str(aggregate_id) != event.aggregate_id or str(event_id) != event.event_id:
            raise ValueError("persisted UUIDs must match the domain event identities")
        if event.sequence != expected_version + 1:
            raise OptimisticConcurrencyError("event sequence does not follow expected version")
        if (lease_key is None) is not (lease_epoch is None):
            raise ValueError("lease key and epoch must be supplied together")
        if lease_key is not None:
            lease_valid = (
                await connection.execute(
                    sa.select(window_lease.c.lease_epoch)
                    .where(
                        window_lease.c.lease_key == lease_key,
                        window_lease.c.lease_epoch == lease_epoch,
                        window_lease.c.expires_at > sa.func.clock_timestamp(),
                    )
                    .with_for_update(read=True)
                )
            ).one_or_none()
            if lease_valid is None:
                raise StaleLeaseError("live event rejected for stale fencing token")

        await connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:aggregate_key, 0))"),
            {"aggregate_key": f"{event.aggregate_type}:{aggregate_id}"},
        )

        current_version = await connection.scalar(
            sa.select(aggregate_projection.c.row_version).where(
                aggregate_projection.c.aggregate_type == event.aggregate_type,
                aggregate_projection.c.aggregate_id == aggregate_id,
            )
        )
        actual_version = 0 if current_version is None else int(current_version)
        if actual_version != expected_version:
            raise OptimisticConcurrencyError(
                f"expected version {expected_version}, found {actual_version}"
            )

        payload = dict(event.payload)
        occurred_seconds, occurred_nanoseconds = divmod(event.occurred_at_ns, 1_000_000_000)
        occurred_at = datetime.fromtimestamp(occurred_seconds, UTC).replace(
            microsecond=occurred_nanoseconds // 1_000
        )
        await connection.execute(
            domain_event.insert().values(
                event_id=event_id,
                event_type=event.event_type,
                event_schema_version=1,
                aggregate_type=event.aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_seq=event.sequence,
                occurred_at=occurred_at,
                occurred_at_ns=event.occurred_at_ns,
                actor_type=actor_type,
                actor_id=event.actor,
                correlation_id=correlation_id,
                causation_id=causation_id,
                reason_codes=[item.value for item in event.reason_codes],
                payload=payload,
                payload_hash=canonical_sha256(payload),
                runtime_manifest_hash=runtime_manifest_hash,
                lease_key=lease_key,
                lease_epoch=lease_epoch,
            )
        )
        projection_statement = insert(aggregate_projection).values(
            aggregate_type=event.aggregate_type,
            aggregate_id=aggregate_id,
            state=projection_state,
            row_version=event.sequence,
            last_event_id=event_id,
        )
        if expected_version == 0:
            changed = (
                await connection.execute(
                    projection_statement.on_conflict_do_nothing(
                        index_elements=[
                            aggregate_projection.c.aggregate_type,
                            aggregate_projection.c.aggregate_id,
                        ]
                    ).returning(aggregate_projection.c.row_version)
                )
            ).one_or_none()
        else:
            changed = (
                await connection.execute(
                    sa.update(aggregate_projection)
                    .where(
                        aggregate_projection.c.aggregate_type == event.aggregate_type,
                        aggregate_projection.c.aggregate_id == aggregate_id,
                        aggregate_projection.c.row_version == expected_version,
                    )
                    .values(
                        state=projection_state,
                        row_version=event.sequence,
                        last_event_id=event_id,
                        updated_at=sa.func.now(),
                    )
                    .returning(aggregate_projection.c.row_version)
                )
            ).one_or_none()
        if changed is None:
            raise OptimisticConcurrencyError("projection version changed during append")
        await connection.execute(
            outbox_event.insert().values(
                event_id=event_id,
                topic=topic,
                payload={
                    "aggregate_id": str(aggregate_id),
                    "aggregate_type": event.aggregate_type,
                    "event_type": event.event_type,
                    "sequence": event.sequence,
                },
            )
        )
        return Projection(
            event.aggregate_type, aggregate_id, projection_state, event.sequence, event_id
        )

    async def append_audit(
        self,
        connection: AsyncConnection,
        *,
        audit_id: UUID,
        event_type: str,
        actor_id: str,
        correlation_id: UUID,
        payload: Mapping[str, Any],
    ) -> None:
        await connection.execute(
            audit_event.insert().values(
                audit_id=audit_id,
                event_type=event_type,
                actor_id=actor_id,
                correlation_id=correlation_id,
                payload=dict(payload),
                payload_hash=canonical_sha256(payload),
            )
        )

    async def rebuild_projections(self, connection: AsyncConnection) -> list[Projection]:
        await connection.execute(sa.delete(aggregate_projection))
        latest = (
            sa.select(
                domain_event.c.aggregate_type,
                domain_event.c.aggregate_id,
                domain_event.c.aggregate_seq,
                domain_event.c.event_id,
                domain_event.c.payload,
                sa.func.row_number()
                .over(
                    partition_by=(
                        domain_event.c.aggregate_type,
                        domain_event.c.aggregate_id,
                    ),
                    order_by=domain_event.c.aggregate_seq.desc(),
                )
                .label("rank"),
            )
        ).subquery()
        rows = (await connection.execute(sa.select(latest).where(latest.c.rank == 1))).mappings()
        rebuilt: list[Projection] = []
        for row in rows:
            state = row["payload"].get("to_state")
            if not isinstance(state, str):
                raise ValueError("projection event is missing a string to_state")
            item = Projection(
                row["aggregate_type"],
                row["aggregate_id"],
                state,
                row["aggregate_seq"],
                row["event_id"],
            )
            await connection.execute(
                aggregate_projection.insert().values(
                    aggregate_type=item.aggregate_type,
                    aggregate_id=item.aggregate_id,
                    state=item.state,
                    row_version=item.row_version,
                    last_event_id=item.last_event_id,
                )
            )
            rebuilt.append(item)
        return rebuilt


class EvidenceRepository:
    async def record_market_object(
        self,
        connection: AsyncConnection,
        *,
        object_id: UUID,
        stored: StoredObject,
        schema_id: str,
        evidence_class: EvidenceClass,
        source_range: Mapping[str, Any],
    ) -> None:
        await connection.execute(
            market_object.insert().values(
                object_id=object_id,
                object_key=stored.key,
                generation=stored.generation,
                metageneration=stored.metageneration,
                size_bytes=stored.size,
                crc32c=stored.crc32c,
                sha256=stored.sha256,
                schema_id=schema_id,
                evidence_class=evidence_class.value,
                source_range=dict(source_range),
            )
        )

    async def record_dataset_manifest(
        self,
        connection: AsyncConnection,
        *,
        manifest_id: UUID,
        dataset_id: UUID,
        stored: StoredObject,
        evidence_class: EvidenceClass,
        object_count: int,
    ) -> None:
        await connection.execute(
            dataset_manifest.insert().values(
                manifest_id=manifest_id,
                dataset_id=dataset_id,
                manifest_key=stored.key,
                manifest_hash=stored.sha256,
                generation=stored.generation,
                evidence_class=evidence_class.value,
                object_count=object_count,
            )
        )

    async def expectations(
        self, connection: AsyncConnection, *, prefix: str | None = None
    ) -> Sequence[MarketObjectExpectation]:
        statement = sa.select(market_object)
        if prefix is not None:
            statement = statement.where(market_object.c.object_key.startswith(prefix))
        rows = (await connection.execute(statement)).mappings()
        return [
            MarketObjectExpectation(
                object_key=row["object_key"],
                generation=row["generation"],
                metageneration=row["metageneration"],
                size=row["size_bytes"],
                crc32c=row["crc32c"],
                sha256=row["sha256"],
                evidence_class=EvidenceClass(row["evidence_class"]),
            )
            for row in rows
        ]
