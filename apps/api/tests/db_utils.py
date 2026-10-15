"""Helpers for tests that need a real Postgres (docker-compose locally, service container in CI)."""

import os
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
ROOT_ENV_FILE = API_ROOT.parents[1] / ".env"


def _setting(name: str) -> str | None:
    """A variable from the real environment, else from the repo-root .env (never mutating
    os.environ). Settings reads that .env itself, but pytest helpers such as POSTGRES_PORT
    are not Settings fields, so they need this."""
    return os.environ.get(name) or dotenv_values(ROOT_ENV_FILE).get(name)


def get_test_database_url() -> str:
    """URL of the shared test database. It must be named *_test: tests wipe it.

    Order: TEST_DATABASE_URL, then DATABASE_URL if it already points at a *_test database
    (that is what CI sets), then the docker-compose default. POSTGRES_PORT is honoured (from
    the environment or the root .env) when 5432 is taken on your machine.
    """
    for name in ("TEST_DATABASE_URL", "DATABASE_URL"):
        url = _setting(name)
        if url and (make_url(url).database or "").endswith("_test"):
            return url
    port = _setting("POSTGRES_PORT") or "5432"
    return f"postgresql+asyncpg://codelens:codelens@localhost:{port}/codelens_test"


def _admin_url(url: str):  # type: ignore[no-untyped-def]
    """Same server, but the always-present `postgres` maintenance database."""
    return make_url(url).set(database="postgres")


async def create_database(url: str, *, if_missing: bool = False) -> None:
    """CREATE DATABASE for the database named in `url`."""
    name = make_url(url).database
    assert name and name.replace("_", "").isalnum(), f"unsafe database name: {name!r}"
    engine = create_async_engine(_admin_url(url), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            if if_missing:
                exists = await conn.scalar(
                    text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
                )
                if exists:
                    return
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


async def drop_database(url: str) -> None:
    name = make_url(url).database
    assert name and name.endswith("_test"), f"refusing to drop {name!r}"
    engine = create_async_engine(_admin_url(url), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        await engine.dispose()


def scratch_database_url(base_url: str) -> str:
    """A unique throwaway database name on the same server (for migration up/down tests)."""
    return (
        make_url(base_url)
        .set(database=f"migrations_{uuid.uuid4().hex[:10]}_test")
        .render_as_string(hide_password=False)
    )


def alembic_config(url: str) -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)
