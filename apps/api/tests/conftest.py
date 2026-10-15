from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.main import create_app
from tests import db_utils


@pytest.fixture
def settings() -> Settings:
    # _env_file=None so a developer's local .env can never leak into tests.
    return Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        web_origin="http://localhost:5173",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    # raise_server_exceptions=False: we want to assert on the 500 response the client would get.
    return TestClient(create_app(settings), raise_server_exceptions=False)


# --- real Postgres fixtures ---------------------------------------------------------------


@pytest.fixture(scope="session")
def test_db_url() -> str:
    return db_utils.get_test_database_url()


@pytest_asyncio.fixture(scope="session")
async def migrated_db(test_db_url: str) -> str:
    """Create codelens_test if missing (an old volume skipped the init script), wipe it and
    migrate it to head. Runs once per test session."""
    await db_utils.create_database(test_db_url, if_missing=True)
    wipe = create_async_engine(test_db_url)
    async with wipe.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await wipe.dispose()
    # Alembic's env.py calls asyncio.run(), which cannot nest inside the running test loop.
    import asyncio

    await asyncio.to_thread(db_utils.upgrade, test_db_url)
    return test_db_url


@pytest_asyncio.fixture(scope="session")
async def engine(migrated_db: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_db, pool_size=20, max_overflow=10)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Factory over a freshly emptied database.

    Tables are truncated (not rolled back) because the queue tests need several real
    connections committing independently, which a single wrapping transaction cannot model.
    """
    async with engine.begin() as conn:
        tables = await conn.scalars(
            text(
                "SELECT quote_ident(tablename) FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s
