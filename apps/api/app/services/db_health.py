"""Database reachability and migration-revision check, shared by /health and /ready."""

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

logger = logging.getLogger("app.health")

CHECK_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class DatabaseStatus:
    available: bool
    # Current Alembic revision; None if the DB is down or has never been migrated.
    revision: str | None


async def check_database(engine: AsyncEngine) -> DatabaseStatus:
    """Probe the database without ever raising and without hanging.

    The timeout matters: a half-dead connection can block for minutes, and a liveness probe
    that hangs is worse than one that says "unavailable".
    """
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                return DatabaseStatus(available=True, revision=await _revision(conn))
    except (OSError, TimeoutError, DBAPIError):
        # Log the exception type only: driver messages can contain the host and user name.
        logger.warning("database check failed", exc_info=False, extra={"reason": "unreachable"})
        return DatabaseStatus(available=False, revision=None)


async def _revision(conn: AsyncConnection) -> str | None:
    try:
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        row = result.first()
        return str(row[0]) if row else None
    except ProgrammingError:
        # alembic_version does not exist: the database is up but was never migrated.
        return None
