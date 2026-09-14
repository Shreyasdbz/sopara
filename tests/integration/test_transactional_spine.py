from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine

from sopara.adapters.database.engine import Database, PoolLimits, create_database_engine
from sopara.adapters.database.errors import (
    IdempotencyConflictError,
    OptimisticConcurrencyError,
    StaleLeaseError,
)
from sopara.adapters.database.repositories import (
    CommandReceipt,
    CommandRepository,
    EventRepository,
    EvidenceRepository,
    LeaseRepository,
)
from sopara.adapters.database.schema import aggregate_projection, domain_event, outbox_event
from sopara.adapters.evidence.artifacts import seal_market_artifact
from sopara.adapters.evidence.reconciliation import MismatchKind, reconcile_objects
from sopara.adapters.evidence.stores import MemoryObjectStore
from sopara.domain.events import DOMAIN_EVENT_SCHEMA_VERSION, DomainEvent
from sopara.domain.evidence import EvidenceClass
from sopara.domain.reason_codes import ReasonCode


def event(aggregate_id: UUID, event_id: UUID, sequence: int, state: str) -> DomainEvent:
    return DomainEvent(
        schema_version=DOMAIN_EVENT_SCHEMA_VERSION,
        event_id=str(event_id),
        aggregate_id=str(aggregate_id),
        aggregate_type="experiment",
        sequence=sequence,
        event_type="experiment.state_changed",
        occurred_at_ns=1_789_000_000_000_000_000 + sequence,
        actor="owner-hash",
        reason_codes=(ReasonCode.INVALID_TRANSITION,),
        payload=(("to_state", state),),
    )


async def append(
    database: Database,
    repository: EventRepository,
    aggregate_id: UUID,
    event_id: UUID,
    sequence: int,
    state: str,
    *,
    lease_key: str | None = None,
    lease_epoch: int | None = None,
) -> None:
    async with database.transaction() as connection:
        await repository.append(
            connection,
            event=event(aggregate_id, event_id, sequence, state),
            aggregate_id=aggregate_id,
            event_id=event_id,
            correlation_id=uuid4(),
            causation_id=uuid4(),
            actor_type="OWNER",
            runtime_manifest_hash="a" * 64,
            projection_state=state,
            expected_version=sequence - 1,
            topic="experiment",
            lease_key=lease_key,
            lease_epoch=lease_epoch,
        )


async def append_then_fail(
    database: Database,
    repository: EventRepository,
    aggregate_id: UUID,
    event_id: UUID,
) -> None:
    async with database.transaction() as connection:
        await repository.append(
            connection,
            event=event(aggregate_id, event_id, 1, "RUNNING"),
            aggregate_id=aggregate_id,
            event_id=event_id,
            correlation_id=uuid4(),
            causation_id=uuid4(),
            actor_type="OWNER",
            runtime_manifest_hash="a" * 64,
            projection_state="RUNNING",
            expected_version=0,
            topic="experiment",
        )
        raise RuntimeError("after append")


async def ledger_write_then_fail(
    database: Database,
    aggregate_id: UUID,
    event_id: UUID,
) -> None:
    item = event(aggregate_id, event_id, 1, "RUNNING")
    async with database.transaction() as connection:
        await connection.execute(
            domain_event.insert().values(
                event_id=event_id,
                event_type=item.event_type,
                event_schema_version=1,
                aggregate_type=item.aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_seq=item.sequence,
                occurred_at=datetime.fromtimestamp(item.occurred_at_ns / 1_000_000_000, UTC),
                occurred_at_ns=item.occurred_at_ns,
                actor_type="OWNER",
                actor_id=item.actor,
                correlation_id=uuid4(),
                causation_id=uuid4(),
                reason_codes=[ReasonCode.INVALID_TRANSITION.value],
                payload={"to_state": "RUNNING"},
                payload_hash="a" * 64,
                runtime_manifest_hash="a" * 64,
            )
        )
        raise RuntimeError("before outbox")


@pytest.mark.asyncio
async def test_duplicate_commands_converge_and_conflicts_fail(
    database_engine: AsyncEngine,
) -> None:
    database = Database(database_engine)
    repository = CommandRepository()
    target_id = uuid4()

    async def receive(command_id: UUID, payload_hash: str) -> CommandReceipt:
        async with database.transaction() as connection:
            return await repository.receive(
                connection,
                command_id=command_id,
                idempotency_key="start-once",
                payload_hash=payload_hash,
                command_type="START_REPLAY",
                target_type="experiment",
                target_id=target_id,
                expected_version=0,
                actor_email_hash="owner-hash",
            )

    receipts = await asyncio.gather(receive(uuid4(), "a" * 64), receive(uuid4(), "a" * 64))
    assert len({item.command_id for item in receipts}) == 1
    assert sorted(item.duplicate for item in receipts) == [False, True]
    with pytest.raises(IdempotencyConflictError):
        await receive(uuid4(), "b" * 64)


@pytest.mark.asyncio
async def test_event_projection_outbox_are_atomic_and_ledger_is_append_only(
    database_engine: AsyncEngine,
) -> None:
    database = Database(database_engine)
    repository = EventRepository()
    aggregate_id = uuid4()
    event_id = uuid4()
    before_outbox_event_id = uuid4()
    with pytest.raises(RuntimeError, match="before outbox"):
        await ledger_write_then_fail(database, aggregate_id, before_outbox_event_id)
    with pytest.raises(RuntimeError, match="after append"):
        await append_then_fail(database, repository, aggregate_id, event_id)
    async with database_engine.connect() as connection:
        assert (
            await connection.scalar(
                sa.select(sa.func.count())
                .select_from(domain_event)
                .where(domain_event.c.event_id == event_id)
            )
            == 0
        )
        assert (
            await connection.scalar(
                sa.select(sa.func.count())
                .select_from(domain_event)
                .where(domain_event.c.event_id == before_outbox_event_id)
            )
            == 0
        )
        assert (
            await connection.scalar(
                sa.select(sa.func.count())
                .select_from(outbox_event)
                .where(outbox_event.c.event_id == event_id)
            )
            == 0
        )

    await append(database, repository, aggregate_id, event_id, 1, "RUNNING")
    with pytest.raises(DBAPIError, match="append-only"):
        async with database.transaction() as connection:
            await connection.execute(
                sa.update(domain_event)
                .where(domain_event.c.event_id == event_id)
                .values(event_type="bad")
            )


@pytest.mark.asyncio
async def test_optimistic_versions_and_projection_rebuild(database_engine: AsyncEngine) -> None:
    database = Database(database_engine)
    repository = EventRepository()
    aggregate_id = uuid4()
    await append(database, repository, aggregate_id, uuid4(), 1, "RUNNING")
    await append(database, repository, aggregate_id, uuid4(), 2, "COMPLETED")
    with pytest.raises(OptimisticConcurrencyError):
        await append(database, repository, aggregate_id, uuid4(), 2, "FAILED")

    async with database.transaction() as connection:
        before = (
            (
                await connection.execute(
                    sa.select(aggregate_projection).where(
                        aggregate_projection.c.aggregate_id == aggregate_id
                    )
                )
            )
            .mappings()
            .one()
        )
        rebuilt = await repository.rebuild_projections(connection)
        after = (
            (
                await connection.execute(
                    sa.select(aggregate_projection).where(
                        aggregate_projection.c.aggregate_id == aggregate_id
                    )
                )
            )
            .mappings()
            .one()
        )
    assert before["state"] == after["state"] == "COMPLETED"
    assert before["row_version"] == after["row_version"] == 2
    assert any(item.aggregate_id == aggregate_id for item in rebuilt)


@pytest.mark.asyncio
async def test_concurrent_writers_are_serialized_by_expected_version(
    database_engine: AsyncEngine,
) -> None:
    database = Database(database_engine)
    repository = EventRepository()
    aggregate_id = uuid4()
    await append(database, repository, aggregate_id, uuid4(), 1, "RUNNING")
    results = await asyncio.gather(
        append(database, repository, aggregate_id, uuid4(), 2, "COMPLETED"),
        append(database, repository, aggregate_id, uuid4(), 2, "FAILED"),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, OptimisticConcurrencyError) for result in results) == 1
    async with database_engine.connect() as connection:
        assert (
            await connection.scalar(
                sa.select(sa.func.count())
                .select_from(domain_event)
                .where(domain_event.c.aggregate_id == aggregate_id)
            )
            == 2
        )


@pytest.mark.asyncio
async def test_lease_theft_fences_stale_writer(database_engine: AsyncEngine) -> None:
    database = Database(database_engine)
    leases = LeaseRepository()
    events = EventRepository()
    lease_key = f"2026-09-10:AM:{uuid4()}"
    async with database.transaction() as connection:
        first = await leases.acquire(
            connection, lease_key=lease_key, holder_execution="execution-1", ttl_seconds=60
        )
    assert first is not None
    async with database.transaction() as connection:
        await connection.execute(
            sa.text(
                "UPDATE sopara.window_lease "
                "SET acquired_at = clock_timestamp() - interval '2 minutes', "
                "expires_at = clock_timestamp() - interval '1 minute' "
                "WHERE lease_key = :lease_key"
            ),
            {"lease_key": lease_key},
        )
    async with database.transaction() as connection:
        second = await leases.acquire(
            connection, lease_key=lease_key, holder_execution="execution-2", ttl_seconds=60
        )
    assert second is not None
    assert second.lease_epoch == first.lease_epoch + 1
    with pytest.raises(StaleLeaseError):
        await append(
            database,
            events,
            uuid4(),
            uuid4(),
            1,
            "RUNNING",
            lease_key=lease_key,
            lease_epoch=first.lease_epoch,
        )


@pytest.mark.asyncio
async def test_durable_object_expectation_reconciles_and_missing_object_fails(
    database_engine: AsyncEngine,
) -> None:
    database = Database(database_engine)
    repository = EvidenceRepository()
    store = MemoryObjectStore(EvidenceClass.SYNTHETIC_NES_PROXY)
    sealed = seal_market_artifact(
        store,
        prefix="objects/raw",
        trade_date="2026-09-10",
        instrument="NES",
        evidence_class=EvidenceClass.SYNTHETIC_NES_PROXY,
        rows=[
            {
                "source_id": "source-1",
                "channel_id": "prices",
                "source_session_id": "session-1",
                "source_sequence": 1,
                "message_index": 0,
                "contract_id": "NES-2026-09-18",
                "instrument": "NES",
                "price_ticks": 12000,
                "quantity": 1,
                "source_time_ns": 1_789_000_000_000_000_000,
                "receive_time_ns": 1_789_000_000_000_000_001,
            }
        ],
    )
    async with database.transaction() as connection:
        await repository.record_market_object(
            connection,
            object_id=uuid4(),
            stored=sealed.stored,
            schema_id=sealed.schema_id,
            evidence_class=sealed.evidence_class,
            source_range={"first_sequence": 1, "last_sequence": 1},
        )
    async with database_engine.connect() as connection:
        expectations = await repository.expectations(connection, prefix="objects/raw/")
    assert reconcile_objects(store, expectations).accepted is True
    missing = reconcile_objects(MemoryObjectStore(EvidenceClass.SYNTHETIC_NES_PROXY), expectations)
    assert missing.accepted is False
    assert MismatchKind.MISSING_OBJECT in {item.kind for item in missing.mismatches}


@pytest.mark.asyncio
async def test_pool_exhaustion_lock_timeout_and_cancellation() -> None:
    import os

    engine = create_database_engine(
        os.environ["SOPARA_DATABASE_URL"], PoolLimits(size=1, timeout_seconds=0.1)
    )
    try:
        async with engine.connect():
            with pytest.raises(SQLAlchemyTimeoutError):
                async with engine.connect():
                    pass
    finally:
        await engine.dispose()

    engine = create_database_engine(
        os.environ["SOPARA_DATABASE_URL"], PoolLimits(size=2, timeout_seconds=1)
    )
    key = f"lock-{uuid4()}"
    try:
        async with engine.begin() as setup:
            await setup.execute(
                sa.text(
                    "INSERT INTO sopara.window_lease "
                    "(lease_key, lease_epoch, holder_execution, acquired_at, "
                    "expires_at, last_heartbeat_at) "
                    "VALUES (:key, 1, 'holder', clock_timestamp(), "
                    "clock_timestamp() + interval '1 minute', clock_timestamp())"
                ),
                {"key": key},
            )
        async with engine.connect() as first, engine.connect() as second:
            transaction = await first.begin()
            await first.execute(
                sa.text(
                    "UPDATE sopara.window_lease SET holder_execution='locked' WHERE lease_key=:key"
                ),
                {"key": key},
            )
            await second.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError):
                await second.execute(
                    sa.text(
                        "UPDATE sopara.window_lease SET holder_execution='blocked' "
                        "WHERE lease_key=:key"
                    ),
                    {"key": key},
                )
            await second.rollback()
            await transaction.rollback()

        task = asyncio.create_task(_sleep_query(engine))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with engine.connect() as connection:
            assert await connection.scalar(sa.text("SELECT 1")) == 1
    finally:
        await engine.dispose()


async def _sleep_query(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(sa.text("SELECT pg_sleep(5)"))
