from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine

from sopara.adapters.database.engine import REPLAY_POOL, create_database_engine


@pytest_asyncio.fixture
async def database_engine() -> AsyncIterator[AsyncEngine]:
    url = os.environ.get("SOPARA_DATABASE_URL")
    if url is None:
        raise RuntimeError("integration tests require SOPARA_DATABASE_URL")
    engine = create_database_engine(url, REPLAY_POOL)
    yield engine
    await engine.dispose()
