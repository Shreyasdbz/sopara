from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sopara.adapters.database.repositories import CommandReceipt


class ControlPlaneUnavailableError(Exception):
    pass


class ResourceNotFoundError(Exception):
    pass


class StaleResourceVersionError(Exception):
    def __init__(self, current_version: int) -> None:
        super().__init__(f"resource version is stale; current version is {current_version}")
        self.current_version = current_version


class InvalidCommandError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ReadPage:
    items: Sequence[Mapping[str, object]]
    next_value: str | None
    snapshot_revision: int


@dataclass(frozen=True, slots=True)
class ReadinessRow:
    revision: int
    stream_sequence: int
    generated_at: datetime
    last_domain_event_at: datetime | None
    last_ingest_at: datetime | None
    evidence_class: str
    source_health: str
    window_state: str
    incident_count: int


@dataclass(frozen=True, slots=True)
class StreamEvent:
    sequence: int
    topic: str
    payload: Mapping[str, object]
    committed_at: datetime
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class StreamWindow:
    minimum_sequence: int | None
    maximum_sequence: int | None
    events: Sequence[StreamEvent]


class ControlPlanePort(Protocol):
    async def ping(self) -> None: ...

    async def readiness(self) -> ReadinessRow: ...

    async def page(self, collection: str, *, after: str | None, limit: int) -> ReadPage: ...

    async def detail(self, collection: str, resource_id: UUID) -> Mapping[str, object]: ...

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
    ) -> CommandReceipt: ...

    async def stream_window(self, *, after: int, limit: int) -> StreamWindow: ...
