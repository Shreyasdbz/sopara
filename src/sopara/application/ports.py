from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sopara.domain.evidence import EvidenceClass


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    generation: int
    metageneration: int
    size: int
    crc32c: str
    sha256: str
    content_type: str
    metadata: Mapping[str, str]
    created: bool


@dataclass(frozen=True, slots=True)
class MarketObjectExpectation:
    object_key: str
    generation: int
    metageneration: int
    size: int
    crc32c: str
    sha256: str
    evidence_class: EvidenceClass


@dataclass(frozen=True, slots=True)
class ReplayArtifact:
    instrument: str
    row_count: int
    stored: StoredObject


@dataclass(frozen=True, slots=True)
class DatasetRow:
    dataset_id: UUID
    manifest_hash: str
    manifest_key: str
    generation: int
    evidence_class: EvidenceClass


@dataclass(frozen=True, slots=True)
class ExperimentRow:
    experiment_id: UUID
    experiment_hash: str
    evidence_class: EvidenceClass
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class RunRow:
    run_id: UUID
    dataset_manifest_hash: str
    experiment_hash: str
    runtime_manifest_hash: str
    evidence_class: EvidenceClass
    state: str
    last_input_index: int
    checkpoint_hash: str | None
    result_object_key: str | None
    result_generation: int | None
    result_hash: str | None
    row_version: int


@dataclass(frozen=True, slots=True)
class CheckpointRow:
    checkpoint_id: UUID
    run_id: UUID
    input_index: int
    input_key: str
    source_watermarks: Mapping[str, object]
    engine_state: Mapping[str, object]
    engine_state_hash: str
    preceding_output_hash: str
    runtime_manifest_hash: str
    task_index: int
    checkpoint_hash: str


class ObjectNotFoundError(LookupError):
    pass


class ObjectStore(Protocol):
    def put_if_absent(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> StoredObject: ...

    def stat(self, key: str) -> StoredObject: ...

    def read(self, key: str) -> bytes: ...

    def list(self, prefix: str) -> tuple[StoredObject, ...]: ...


class HistoricalRepositoryPort(Protocol):
    async def register_dataset(
        self,
        *,
        dataset_id: UUID,
        manifest_id: UUID,
        manifest: StoredObject,
        evidence_class: EvidenceClass,
        artifacts: Sequence[ReplayArtifact],
    ) -> DatasetRow: ...

    async def dataset(self, manifest_hash: str) -> DatasetRow: ...

    async def create_experiment(
        self,
        *,
        experiment_id: UUID,
        experiment_hash: str,
        evidence_class: EvidenceClass,
        payload: Mapping[str, object],
    ) -> ExperimentRow: ...

    async def experiment(self, experiment_hash: str) -> ExperimentRow: ...

    async def create_run(
        self,
        *,
        run_id: UUID,
        dataset_manifest_hash: str,
        experiment_hash: str,
        runtime_manifest_hash: str,
        evidence_class: EvidenceClass,
    ) -> RunRow: ...

    async def run(self, run_id: UUID) -> RunRow: ...

    async def begin(self, run_id: UUID, *, allow_resume: bool) -> RunRow: ...

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
    ) -> None: ...

    async def latest_checkpoint(self, run_id: UUID) -> CheckpointRow | None: ...

    async def checkpoints(self, run_id: UUID) -> tuple[CheckpointRow, ...]: ...

    async def interrupt(self, run_id: UUID) -> RunRow: ...

    async def complete(
        self,
        *,
        run_id: UUID,
        evidence_class: EvidenceClass,
        result_object: StoredObject,
        result_hash: str,
        records: Mapping[str, Sequence[Mapping[str, object]]],
    ) -> RunRow: ...

    async def records(self, run_id: UUID) -> dict[str, tuple[Mapping[str, object], ...]]: ...

    async def record_reconciliation(
        self,
        *,
        run_id: UUID,
        evidence_class: EvidenceClass,
        payload: Mapping[str, object],
        accepted: bool,
        finalize: bool = True,
    ) -> RunRow: ...
