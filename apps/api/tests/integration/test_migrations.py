"""Migrations run against throwaway databases, never the shared test database."""

import asyncio
from collections.abc import AsyncIterator

import pytest_asyncio
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests import db_utils

EXPECTED_TABLES = {
    "users",
    "installations",
    "installation_members",
    "repositories",
    "pull_requests",
    "review_runs",
    "findings",
    "code_chunks",
    "chat_sessions",
    "chat_messages",
    "webhook_deliveries",
    "jobs",
    "refresh_tokens",
}


@pytest_asyncio.fixture
async def scratch_url(test_db_url: str) -> AsyncIterator[str]:
    url = db_utils.scratch_database_url(test_db_url)
    await db_utils.create_database(url)
    yield url
    await db_utils.drop_database(url)


async def _scalars(url: str, sql: str) -> list[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return [str(v) for v in await conn.scalars(text(sql))]
    finally:
        await engine.dispose()


async def _tables(url: str) -> set[str]:
    return set(
        await _scalars(url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    ) - {"alembic_version"}


async def test_upgrade_on_a_fresh_database_creates_everything(scratch_url: str) -> None:
    await asyncio.to_thread(db_utils.upgrade, scratch_url)

    assert await _tables(scratch_url) == EXPECTED_TABLES
    head = ScriptDirectory.from_config(db_utils.alembic_config(scratch_url)).get_current_head()
    assert await _scalars(scratch_url, "SELECT version_num FROM alembic_version") == [head]
    assert await _scalars(scratch_url, "SELECT extname FROM pg_extension WHERE extname='vector'")


async def test_search_indexes_exist_with_the_right_access_methods(scratch_url: str) -> None:
    await asyncio.to_thread(db_utils.upgrade, scratch_url)

    engine = create_async_engine(scratch_url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'code_chunks'")
        )
        defs = dict(rows.all())
    await engine.dispose()
    assert "USING hnsw" in defs["ix_code_chunks_embedding_hnsw"]
    assert "vector_cosine_ops" in defs["ix_code_chunks_embedding_hnsw"]
    assert "USING gin" in defs["ix_code_chunks_tsv"]


async def test_downgrade_to_base_removes_all_tables(scratch_url: str) -> None:
    await asyncio.to_thread(db_utils.upgrade, scratch_url)
    await asyncio.to_thread(db_utils.downgrade, scratch_url)

    assert await _tables(scratch_url) == set()


async def test_upgrade_downgrade_upgrade_round_trip(scratch_url: str) -> None:
    await asyncio.to_thread(db_utils.upgrade, scratch_url)
    await asyncio.to_thread(db_utils.downgrade, scratch_url)
    await asyncio.to_thread(db_utils.upgrade, scratch_url)

    assert await _tables(scratch_url) == EXPECTED_TABLES


async def test_models_and_migrations_have_not_drifted(scratch_url: str) -> None:
    """Fails if someone changes a model without writing a migration (alembic check)."""
    await asyncio.to_thread(db_utils.upgrade, scratch_url)

    await asyncio.to_thread(command.check, db_utils.alembic_config(scratch_url))
