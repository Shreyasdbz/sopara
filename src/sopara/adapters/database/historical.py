# pyright: reportAssignmentType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from sopara.adapters.database.engine import Database
from sopara.adapters.database.schema import (
    cost_projection,
    dataset_manifest,
    decision,
    experiment,
    export_manifest,
    feature_snapshot,
    market_object,
    policy_check,
    position_projection,
    reconciliation,
    replay_checkpoint,
    replay_run,
    sim_fill,
    sim_order_event,
)
from sopara.application.identities import stable_uuid
from sopara.application.ports import (
    CheckpointRow,
    DatasetRow,
    ExperimentRow,
    ReplayArtifact,
    RunRow,
    StoredObject,
)
from sopara.domain.canonical import canonical_sha256
from sopara.domain.evidence import EvidenceClass

RECORD_TABLES: dict[str, sa.Table] = {
    "feature_snapshot": feature_snapshot,
    "decision": decision,
    "policy_check": policy_check,
    "sim_order_event": sim_order_event,
    "sim_fill": sim_fill,
    "position_projection": position_projection,
    "cost_projection": cost_projection,
    "export_manifest": export_manifest,
}
EVIDENCE_RECORDS = set(RECORD_TABLES) - {"policy_check"}


def _run(row: sa.RowMapping) -> RunRow:
    return RunRow(
        run_id=row["run_id"],
        dataset_manifest_hash=row["dataset_manifest_hash"],
        experiment_hash=row["experiment_hash"],
        runtime_manifest_hash=row["runtime_manifest_hash"],
        evidence_class=EvidenceClass(row["evidence_class"]),
        state=row["state"],
        last_input_index=row["last_input_index"],
        checkpoint_hash=row["checkpoint_hash"],
        result_object_key=row["result_object_key"],
        result_generation=row["result_generation"],
        result_hash=row["result_hash"],
        row_version=row["row_version"],
    )


class HistoricalRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def register_dataset(
        self,
        *,
        dataset_id: UUID,
        manifest_id: UUID,
        manifest: StoredObject,
        evidence_class: EvidenceClass,
        artifacts: Sequence[ReplayArtifact],
    ) -> DatasetRow:
        async with self._database.transaction() as connection:
            for artifact in artifacts:
                stored = artifact.stored
                await connection.execute(
                    insert(market_object)
                    .values(
                        object_id=stable_uuid("market-object", stored.key),
                        object_key=stored.key,
                        generation=stored.generation,
                        metageneration=stored.metageneration,
                        size_bytes=stored.size,
                        crc32c=stored.crc32c,
                        sha256=stored.sha256,
                        schema_id=stored.metadata["schema_id"],
                        evidence_class=evidence_class.value,
                        source_range={
                            "instrument": artifact.instrument,
                            "row_count": artifact.row_count,
                        },
                    )
                    .on_conflict_do_nothing(constraint="uq_market_object_key")
                )
            await connection.execute(
                insert(dataset_manifest)
                .values(
                    manifest_id=manifest_id,
                    dataset_id=dataset_id,
                    manifest_key=manifest.key,
                    manifest_hash=manifest.sha256,
                    generation=manifest.generation,
                    evidence_class=evidence_class.value,
                    object_count=len(artifacts),
                )
                .on_conflict_do_nothing(constraint="uq_dataset_manifest_hash")
            )
            row = (
                (
                    await connection.execute(
                        sa.select(dataset_manifest).where(
                            dataset_manifest.c.manifest_hash == manifest.sha256
                        )
                    )
                )
                .mappings()
                .one()
            )
        return DatasetRow(
            dataset_id=row["dataset_id"],
            manifest_hash=row["manifest_hash"],
            manifest_key=row["manifest_key"],
            generation=row["generation"],
            evidence_class=EvidenceClass(row["evidence_class"]),
        )

    async def dataset(self, manifest_hash: str) -> DatasetRow:
        async with self._database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        sa.select(dataset_manifest).where(
                            dataset_manifest.c.manifest_hash == manifest_hash
                        )
                    )
                )
                .mappings()
                .one()
            )
        return DatasetRow(
            dataset_id=row["dataset_id"],
            manifest_hash=row["manifest_hash"],
            manifest_key=row["manifest_key"],
            generation=row["generation"],
            evidence_class=EvidenceClass(row["evidence_class"]),
        )

    async def create_experiment(
        self,
        *,
        experiment_id: UUID,
        experiment_hash: str,
        evidence_class: EvidenceClass,
        payload: Mapping[str, object],
    ) -> ExperimentRow:
        async with self._database.transaction() as connection:
            await connection.execute(
                insert(experiment)
                .values(
                    record_id=experiment_id,
                    aggregate_id=experiment_id,
                    evidence_class=evidence_class.value,
                    payload=dict(payload),
                    payload_hash=experiment_hash,
                )
                .on_conflict_do_nothing(constraint="uq_experiment_aggregate_hash")
            )
        return await self.experiment(experiment_hash)

    async def experiment(self, experiment_hash: str) -> ExperimentRow:
        async with self._database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        sa.select(experiment).where(experiment.c.payload_hash == experiment_hash)
                    )
                )
                .mappings()
                .one()
            )
        return ExperimentRow(
            experiment_id=row["aggregate_id"],
            experiment_hash=row["payload_hash"],
            evidence_class=EvidenceClass(row["evidence_class"]),
            payload=cast("Mapping[str, object]", row["payload"]),
        )

    async def create_run(
        self,
        *,
        run_id: UUID,
        dataset_manifest_hash: str,
        experiment_hash: str,
        runtime_manifest_hash: str,
        evidence_class: EvidenceClass,
    ) -> RunRow:
        async with self._database.transaction() as connection:
            await connection.execute(
                insert(replay_run)
                .values(
                    run_id=run_id,
                    dataset_manifest_hash=dataset_manifest_hash,
                    experiment_hash=experiment_hash,
                    runtime_manifest_hash=runtime_manifest_hash,
                    evidence_class=evidence_class.value,
                    state="CREATED",
                )
                .on_conflict_do_nothing(constraint="uq_replay_run_identity")
            )
        return await self.run_by_identity(
            dataset_manifest_hash, experiment_hash, runtime_manifest_hash
        )

    async def run_by_identity(
        self, dataset_hash: str, experiment_hash: str, runtime_hash: str
    ) -> RunRow:
        async with self._database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        sa.select(replay_run).where(
                            replay_run.c.dataset_manifest_hash == dataset_hash,
                            replay_run.c.experiment_hash == experiment_hash,
                            replay_run.c.runtime_manifest_hash == runtime_hash,
                        )
                    )
                )
                .mappings()
                .one()
            )
        return _run(row)

    async def run(self, run_id: UUID) -> RunRow:
        async with self._database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        sa.select(replay_run).where(replay_run.c.run_id == run_id)
                    )
                )
                .mappings()
                .one()
            )
        return _run(row)

    async def begin(self, run_id: UUID, *, allow_resume: bool) -> RunRow:
        allowed = ("CREATED", "INTERRUPTED") if allow_resume else ("CREATED",)
        async with self._database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        sa.update(replay_run)
                        .where(replay_run.c.run_id == run_id, replay_run.c.state.in_(allowed))
                        .values(
                            state="RUNNING",
                            row_version=replay_run.c.row_version + 1,
                            updated_at=sa.func.now(),
                        )
                        .returning(replay_run)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            current = await self.run(run_id)
            raise ValueError(f"run cannot begin from {current.state}")
        return _run(row)

    async def save_checkpoint(
        self,
        *,
        run_id: UUID,
        input_index: int,
        input_key: str,
        source_watermarks: Mapping[str, object],
        engine_state: Mapping[str, object],
        engine_state_hash: str,
        preceding_output_hash: str,
        runtime_manifest_hash: str,
        checkpoint_hash: str,
    ) -> None:
        checkpoint_id = stable_uuid("replay-checkpoint", checkpoint_hash)
        async with self._database.transaction() as connection:
            locked = (
                (
                    await connection.execute(
                        sa.select(replay_run).where(replay_run.c.run_id == run_id).with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if locked["state"] != "RUNNING":
                raise ValueError("checkpoint requires a running replay")
            if input_index != locked["last_input_index"] + 1:
                raise ValueError("checkpoint input index is not the next durable event")
            await connection.execute(
                replay_checkpoint.insert().values(
                    checkpoint_id=checkpoint_id,
                    run_id=run_id,
                    input_index=input_index,
                    input_key=input_key,
                    source_watermarks=dict(source_watermarks),
                    engine_state=dict(engine_state),
                    engine_state_hash=engine_state_hash,
                    preceding_output_hash=preceding_output_hash,
                    runtime_manifest_hash=runtime_manifest_hash,
                    task_index=0,
                    checkpoint_hash=checkpoint_hash,
                )
            )
            await connection.execute(
                sa.update(replay_run)
                .where(replay_run.c.run_id == run_id)
                .values(
                    last_input_index=input_index,
                    checkpoint_hash=checkpoint_hash,
                    row_version=replay_run.c.row_version + 1,
                    updated_at=sa.func.now(),
                )
            )

    async def latest_checkpoint(self, run_id: UUID) -> CheckpointRow | None:
        async with self._database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        sa.select(replay_checkpoint)
                        .where(replay_checkpoint.c.run_id == run_id)
                        .order_by(replay_checkpoint.c.input_index.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return self._checkpoint(row)

    async def checkpoints(self, run_id: UUID) -> tuple[CheckpointRow, ...]:
        async with self._database.engine.connect() as connection:
            rows = (
                await connection.execute(
                    sa.select(replay_checkpoint)
                    .where(replay_checkpoint.c.run_id == run_id)
                    .order_by(replay_checkpoint.c.input_index)
                )
            ).mappings()
            return tuple(self._checkpoint(row) for row in rows)

    @staticmethod
    def _checkpoint(row: sa.RowMapping) -> CheckpointRow:
        return CheckpointRow(
            checkpoint_id=row["checkpoint_id"],
            run_id=row["run_id"],
            input_index=row["input_index"],
            input_key=row["input_key"],
            source_watermarks=cast("Mapping[str, object]", row["source_watermarks"]),
            engine_state=cast("Mapping[str, object]", row["engine_state"]),
            engine_state_hash=row["engine_state_hash"],
            preceding_output_hash=row["preceding_output_hash"],
            runtime_manifest_hash=row["runtime_manifest_hash"],
            task_index=row["task_index"],
            checkpoint_hash=row["checkpoint_hash"],
        )

    async def interrupt(self, run_id: UUID) -> RunRow:
        return await self._set_state(run_id, expected="RUNNING", state="INTERRUPTED")

    async def fail(self, run_id: UUID, reason: str) -> RunRow:
        return await self._set_state(run_id, expected=None, state="FAILED", reason=reason)

    async def _set_state(
        self,
        run_id: UUID,
        *,
        expected: str | None,
        state: str,
        reason: str | None = None,
    ) -> RunRow:
        conditions = [replay_run.c.run_id == run_id]
        if expected is not None:
            conditions.append(replay_run.c.state == expected)
        async with self._database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        sa.update(replay_run)
                        .where(*conditions)
                        .values(
                            state=state,
                            error_reason=reason,
                            row_version=replay_run.c.row_version + 1,
                            updated_at=sa.func.now(),
                        )
                        .returning(replay_run)
                    )
                )
                .mappings()
                .one()
            )
        return _run(row)

    async def complete(
        self,
        *,
        run_id: UUID,
        evidence_class: EvidenceClass,
        result_object: StoredObject,
        result_hash: str,
        records: Mapping[str, Sequence[Mapping[str, object]]],
    ) -> RunRow:
        async with self._database.transaction() as connection:
            locked = (
                (
                    await connection.execute(
                        sa.select(replay_run).where(replay_run.c.run_id == run_id).with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if locked["state"] != "RUNNING":
                raise ValueError("completion requires a running replay")
            await connection.execute(
                insert(market_object)
                .values(
                    object_id=stable_uuid("replay-result-object", result_object.key),
                    object_key=result_object.key,
                    generation=result_object.generation,
                    metageneration=result_object.metageneration,
                    size_bytes=result_object.size,
                    crc32c=result_object.crc32c,
                    sha256=result_object.sha256,
                    schema_id=result_object.metadata["schema_id"],
                    evidence_class=evidence_class.value,
                    source_range={"run_id": str(run_id), "kind": "REPLAY_RESULT"},
                )
                .on_conflict_do_nothing(constraint="uq_market_object_key")
            )
            for record_type, items in records.items():
                table = RECORD_TABLES[record_type]
                for payload in items:
                    payload_hash = canonical_sha256(payload)
                    values: dict[str, Any] = {
                        "record_id": stable_uuid(record_type, payload_hash),
                        "aggregate_id": run_id,
                        "payload": dict(payload),
                        "payload_hash": payload_hash,
                    }
                    if record_type in EVIDENCE_RECORDS:
                        values["evidence_class"] = evidence_class.value
                    await connection.execute(
                        insert(table)
                        .values(**values)
                        .on_conflict_do_nothing(constraint=f"uq_{record_type}_aggregate_hash")
                    )
            row = (
                (
                    await connection.execute(
                        sa.update(replay_run)
                        .where(replay_run.c.run_id == run_id)
                        .values(
                            state="COMPLETED",
                            result_object_key=result_object.key,
                            result_generation=result_object.generation,
                            result_hash=result_hash,
                            row_version=replay_run.c.row_version + 1,
                            updated_at=sa.func.now(),
                        )
                        .returning(replay_run)
                    )
                )
                .mappings()
                .one()
            )
        return _run(row)

    async def records(self, run_id: UUID) -> dict[str, tuple[Mapping[str, object], ...]]:
        result: dict[str, tuple[Mapping[str, object], ...]] = {}
        async with self._database.engine.connect() as connection:
            for name, table in RECORD_TABLES.items():
                rows = (
                    await connection.execute(
                        sa.select(table.c.payload)
                        .where(table.c.aggregate_id == run_id)
                        .order_by(table.c.payload_hash)
                    )
                ).scalars()
                result[name] = tuple(cast("Mapping[str, object]", row) for row in rows)
        return result

    async def record_reconciliation(
        self,
        *,
        run_id: UUID,
        evidence_class: EvidenceClass,
        payload: Mapping[str, object],
        accepted: bool,
        finalize: bool = True,
    ) -> RunRow:
        payload_hash = canonical_sha256(payload)
        async with self._database.transaction() as connection:
            await connection.execute(
                insert(reconciliation)
                .values(
                    record_id=stable_uuid("reconciliation", payload_hash),
                    aggregate_id=run_id,
                    evidence_class=evidence_class.value,
                    payload=dict(payload),
                    payload_hash=payload_hash,
                )
                .on_conflict_do_nothing(constraint="uq_reconciliation_aggregate_hash")
            )
            if finalize or not accepted:
                row = (
                    (
                        await connection.execute(
                            sa.update(replay_run)
                            .where(replay_run.c.run_id == run_id)
                            .values(
                                state="RECONCILED" if accepted else "FAILED",
                                error_reason=None if accepted else "RECONCILIATION_MISMATCH",
                                row_version=replay_run.c.row_version + 1,
                                updated_at=sa.func.now(),
                            )
                            .returning(replay_run)
                        )
                    )
                    .mappings()
                    .one()
                )
            else:
                row = (
                    (
                        await connection.execute(
                            sa.select(replay_run).where(replay_run.c.run_id == run_id)
                        )
                    )
                    .mappings()
                    .one()
                )
        return _run(row)
