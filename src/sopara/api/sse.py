from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

from sopara.api.ports import ControlPlanePort, StreamEvent, StreamWindow


class StreamCursorExpiredError(Exception):
    pass


class StreamGapError(Exception):
    pass


class StreamSchemaChangedError(Exception):
    pass


def validate_window(
    window: StreamWindow, *, after: int, schema_version: int
) -> tuple[StreamEvent, ...]:
    if window.minimum_sequence is not None and after < window.minimum_sequence - 1:
        raise StreamCursorExpiredError("event cursor is outside the retained outbox window")
    accepted: list[StreamEvent] = []
    expected = after + 1
    for event in window.events:
        if event.sequence <= after:
            continue
        if event.sequence != expected:
            raise StreamGapError("event stream contains a cursor gap")
        if event.schema_version != schema_version:
            raise StreamSchemaChangedError("event schema version changed")
        accepted.append(event)
        expected += 1
    return tuple(accepted)


def encode_event(event: StreamEvent, *, schema_version: int) -> bytes:
    data = json.dumps(
        {
            "schemaVersion": schema_version,
            "topic": event.topic,
            "committedAt": event.committed_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "data": event.payload,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"id: {event.sequence}\nevent: {event.topic}\ndata: {data}\n\n".encode()


def encode_heartbeat(now: datetime) -> bytes:
    return f": heartbeat {now.astimezone(UTC).isoformat().replace('+00:00', 'Z')}\n\n".encode()


@dataclass(slots=True)
class EventStreamer:
    repository: ControlPlanePort
    schema_version: int
    poll_seconds: float
    heartbeat_seconds: float
    connection_seconds: float

    async def preflight(self, after: int) -> tuple[StreamEvent, ...]:
        window = await self.repository.stream_window(after=after, limit=100)
        return validate_window(window, after=after, schema_version=self.schema_version)

    async def stream(self, *, after: int, initial: tuple[StreamEvent, ...]) -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        heartbeat_at = started
        cursor = after
        pending = initial
        while loop.time() - started < self.connection_seconds:
            if pending:
                for event in pending:
                    yield encode_event(event, schema_version=self.schema_version)
                    cursor = event.sequence
                pending = ()
                continue
            now = loop.time()
            if now - heartbeat_at >= self.heartbeat_seconds:
                yield encode_heartbeat(datetime.now(UTC))
                heartbeat_at = now
            await asyncio.sleep(self.poll_seconds)
            window = await self.repository.stream_window(after=cursor, limit=100)
            pending = validate_window(window, after=cursor, schema_version=self.schema_version)
        yield b'event: reconnect\ndata: {"reason":"connection_rotation"}\n\n'
