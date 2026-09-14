from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine


@dataclass(frozen=True, slots=True)
class PoolLimits:
    size: int
    overflow: int = 0
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.size <= 0 or self.overflow < 0 or self.timeout_seconds <= 0:
            raise ValueError("pool limits must be positive and overflow cannot be negative")


WEB_POOL = PoolLimits(size=5)
LIVE_POOL = PoolLimits(size=5)
REPLAY_POOL = PoolLimits(size=2)


def create_database_engine(database_url: str, limits: PoolLimits) -> AsyncEngine:
    if not database_url.startswith("postgresql+psycopg://"):
        raise ValueError("canonical database URL must use PostgreSQL through psycopg")
    return create_async_engine(
        database_url,
        pool_size=limits.size,
        max_overflow=limits.overflow,
        pool_timeout=limits.timeout_seconds,
        pool_pre_ping=True,
        connect_args={"options": "-c statement_timeout=10000 -c lock_timeout=2000"},
    )


class Database:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[AsyncConnection]:
        async with self.engine.begin() as connection:
            yield connection

    async def close(self) -> None:
        await self.engine.dispose()
