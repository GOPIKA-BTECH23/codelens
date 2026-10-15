"""Alembic environment, async version (asyncpg has no sync driver)."""

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db import models  # noqa: F401  (registers every table on Base.metadata)
from app.db.base import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    """An explicit sqlalchemy.url (tests point Alembic at throwaway databases) wins;
    otherwise use the app's DATABASE_URL."""
    return (
        config.get_main_option("sqlalchemy.url") or get_settings().database_url.get_secret_value()
    )


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    # NullPool: a migration is a one-off process, no need to keep connections around.
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    raise SystemExit("Offline (--sql) migrations are not supported; run against a database.")
run_migrations_online()
