from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from sopara.adapters.database.engine import Database
from sopara.adapters.database.errors import IdempotencyConflictError
from sopara.adapters.database.schema import experiment
from sopara.api.database import PostgresControlPlaneRepository
from sopara.api.ports import StaleResourceVersionError


@pytest.mark.asyncio
async def test_control_plane_command_receipts_are_atomic_idempotent_and_versioned(
    database_engine: AsyncEngine,
) -> None:
    record_id = uuid4()
    async with database_engine.begin() as connection:
        await connection.execute(
            experiment.insert().values(
                record_id=record_id,
                aggregate_id=record_id,
                evidence_class="SYNTHETIC_NES_PROXY",
                payload={"schema_version": "test"},
                payload_hash="a" * 64,
            )
        )
    repository = PostgresControlPlaneRepository(Database(database_engine))

    async def submit(*, idempotency_key: str, payload_hash: str, expected_version: int) -> object:
        return await repository.submit_command(
            command_id=uuid4(),
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            command_type="START_REPLAY",
            target_type="EXPERIMENT",
            target_id=record_id,
            expected_version=expected_version,
            actor_email_hash="owner-hash",
        )

    first = await repository.submit_command(
        command_id=uuid4(),
        idempotency_key="integration-start-once",
        payload_hash="b" * 64,
        command_type="START_REPLAY",
        target_type="EXPERIMENT",
        target_id=record_id,
        expected_version=1,
        actor_email_hash="owner-hash",
    )
    duplicate = await repository.submit_command(
        command_id=uuid4(),
        idempotency_key="integration-start-once",
        payload_hash="b" * 64,
        command_type="START_REPLAY",
        target_type="EXPERIMENT",
        target_id=record_id,
        expected_version=1,
        actor_email_hash="owner-hash",
    )
    assert first.command_id == duplicate.command_id
    assert first.duplicate is False
    assert duplicate.duplicate is True

    with pytest.raises(IdempotencyConflictError):
        await submit(
            idempotency_key="integration-start-once",
            payload_hash="c" * 64,
            expected_version=1,
        )
    with pytest.raises(StaleResourceVersionError, match="current version is 1"):
        await submit(
            idempotency_key="integration-stale",
            payload_hash="b" * 64,
            expected_version=2,
        )


@pytest.mark.asyncio
async def test_control_plane_outbox_window_uses_durable_sequence(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresControlPlaneRepository(Database(database_engine))
    window = await repository.stream_window(after=0, limit=100)
    sequences = [event.sequence for event in window.events]
    assert sequences == sorted(sequences)
    if sequences:
        assert window.minimum_sequence is not None
        assert window.maximum_sequence is not None
        assert sequences[0] >= window.minimum_sequence
        assert sequences[-1] <= window.maximum_sequence
