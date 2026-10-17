"""The deploy-time migration runner (app/db/migrate.py) and its advisory lock."""

import asyncio
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import MIGRATION_LOCK_KEY, migrate
from tests import db_utils


@pytest_asyncio.fixture
async def scratch_url(test_db_url: str) -> AsyncIterator[str]:
    url = db_utils.scratch_database_url(test_db_url)
    await db_utils.create_database(url)
    yield url
    await db_utils.drop_database(url)


async def _table_count(url: str) -> int:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return int(
                await conn.scalar(text("SELECT count(*) FROM pg_tables WHERE tablename = 'users'"))
                or 0
            )
    finally:
        await engine.dispose()


async def test_migrate_upgrades_a_fresh_database(scratch_url: str) -> None:
    await migrate(scratch_url)

    assert await _table_count(scratch_url) == 1


async def test_migrate_waits_while_another_instance_holds_the_lock(scratch_url: str) -> None:
    holder = create_async_engine(scratch_url, isolation_level="AUTOCOMMIT")
    async with holder.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})

        task = asyncio.create_task(migrate(scratch_url))
        await asyncio.sleep(1.0)
        assert not task.done(), "migration must wait for the lock"
        assert await _table_count(scratch_url) == 0, "nothing may be applied while locked"

        await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
        await asyncio.wait_for(task, timeout=60)
    await holder.dispose()

    assert await _table_count(scratch_url) == 1


async def test_two_instances_starting_together_both_succeed(scratch_url: str) -> None:
    # Without the lock, both would try CREATE TABLE for the same revision and one would fail.
    await asyncio.gather(migrate(scratch_url), migrate(scratch_url))

    assert await _table_count(scratch_url) == 1
