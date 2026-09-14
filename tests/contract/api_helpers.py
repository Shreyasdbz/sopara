from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from sopara.adapters.database.errors import IdempotencyConflictError
from sopara.adapters.database.repositories import CommandReceipt
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


class FakeControlPlaneRepository:
    def __init__(self) -> None:
        self.failure: str | None = None
        self.page_items: tuple[Mapping[str, object], ...] = ()
        self.detail_item: Mapping[str, object] | None = None
        self.next_value: str | None = None
        self.events: tuple[StreamEvent, ...] = ()
        self.minimum_sequence: int | None = None
        self.maximum_sequence: int | None = None
        self.receipt = CommandReceipt(
            UUID("00000000-0000-0000-0000-000000000010"), "RECEIVED", False
        )
        self.command_calls: list[dict[str, object]] = []

    async def ping(self) -> None:
        self._maybe_unavailable()

    async def readiness(self) -> ReadinessRow:
        self._maybe_unavailable()
        return ReadinessRow(
            revision=7,
            stream_sequence=7,
            generated_at=datetime(2026, 9, 10, 12, tzinfo=UTC),
            last_domain_event_at=datetime(2026, 9, 10, 11, 59, tzinfo=UTC),
            last_ingest_at=datetime(2026, 9, 10, 11, 58, tzinfo=UTC),
            evidence_class="SYNTHETIC_NES_PROXY",
            source_health="READY",
            window_state="NO_SESSION",
            incident_count=0,
        )

    async def page(self, collection: str, *, after: str | None, limit: int) -> ReadPage:
        self._maybe_unavailable()
        return ReadPage(self.page_items[:limit], self.next_value, 7)

    async def detail(self, collection: str, resource_id: UUID) -> Mapping[str, object]:
        self._maybe_unavailable()
        if self.detail_item is None:
            raise ResourceNotFoundError("not found")
        return self.detail_item

    async def submit_command(self, **kwargs: object) -> CommandReceipt:
        self._maybe_unavailable()
        self.command_calls.append(kwargs)
        if self.failure == "conflict":
            raise IdempotencyConflictError("conflict")
        if self.failure == "stale":
            raise StaleResourceVersionError(9)
        if self.failure == "invalid":
            raise InvalidCommandError("command type and target type are incompatible")
        return self.receipt

    async def stream_window(self, *, after: int, limit: int) -> StreamWindow:
        self._maybe_unavailable()
        return StreamWindow(
            self.minimum_sequence,
            self.maximum_sequence,
            tuple(event for event in self.events if event.sequence > after)[:limit],
        )

    def _maybe_unavailable(self) -> None:
        if self.failure == "unavailable":
            raise ControlPlaneUnavailableError("database is unavailable")
