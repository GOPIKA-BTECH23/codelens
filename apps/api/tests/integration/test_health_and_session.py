"""/health, /ready and the get_session transaction handling against a real database."""

import asyncio

from alembic.script import ScriptDirectory
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import __version__
from app.config import Settings
from app.core.csrf import CSRF_HEADER, CSRF_HEADER_VALUE
from app.db.models import User
from app.deps import SessionDep
from app.main import create_app
from tests import db_utils
from tests.app_utils import make_settings


def _settings(url: str) -> Settings:
    return make_settings(database_url=url)


CSRF_HEADERS = {CSRF_HEADER: CSRF_HEADER_VALUE}


def _head(url: str) -> str | None:
    return ScriptDirectory.from_config(db_utils.alembic_config(url)).get_current_head()


async def test_health_and_ready_report_database_and_revision(
    migrated_db: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    def call() -> tuple[dict[str, object], dict[str, object], int]:
        with TestClient(create_app(_settings(migrated_db))) as client:
            health, ready = client.get("/health"), client.get("/ready")
            return health.json(), ready.json(), ready.status_code

    health, ready, ready_status = await asyncio.to_thread(call)

    head = _head(migrated_db)
    assert health == {"status": "ok", "version": __version__, "database": "ok", "revision": head}
    assert (ready_status, ready) == (200, {"status": "ready", "revision": head})


def _app_with_routes(url: str) -> FastAPI:
    app = create_app(_settings(url))

    @app.post("/_test/ok")
    async def ok(session: SessionDep) -> dict[str, str]:
        session.add(User(github_id=101, login="committed"))
        return {"status": "done"}

    @app.post("/_test/boom")
    async def boom(session: SessionDep) -> None:
        session.add(User(github_id=102, login="rolled-back"))
        await session.flush()  # the INSERT is sent, but must not survive the error
        raise HTTPException(status_code=400, detail="nope")

    return app


async def test_get_session_commits_when_the_handler_succeeds(
    migrated_db: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    def call() -> int:
        with TestClient(_app_with_routes(migrated_db)) as client:
            return client.post("/_test/ok", headers=CSRF_HEADERS).status_code

    assert await asyncio.to_thread(call) == 200

    async with session_factory() as s:
        assert (
            await s.scalar(select(func.count()).select_from(User).where(User.github_id == 101)) == 1
        )


async def test_get_session_rolls_back_when_the_handler_raises(
    migrated_db: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    def call() -> int:
        with TestClient(_app_with_routes(migrated_db)) as client:
            return client.post("/_test/boom", headers=CSRF_HEADERS).status_code

    assert await asyncio.to_thread(call) == 400

    async with session_factory() as s:
        assert (
            await s.scalar(select(func.count()).select_from(User).where(User.github_id == 102)) == 0
        )
