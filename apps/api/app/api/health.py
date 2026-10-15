"""Liveness (/health) and readiness (/ready) endpoints.

/health is a LIVENESS check: it answers 200 whenever the process is up, even if the database
is down, so Render/Docker do not restart a healthy API just because Postgres blipped. The
database state is reported inside the body.
/ready is a READINESS check: 503 when the database is down, so it can gate traffic.
"""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from app import __version__
from app.core.errors import ServiceUnavailableError
from app.services.db_health import check_database

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    database: Literal["ok", "unavailable"]
    # Current Alembic revision; null when the database is unreachable or not migrated.
    revision: str | None


class ReadyResponse(BaseModel):
    status: Literal["ready"]
    revision: str | None


def _engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


@router.get("/health", response_model=HealthResponse, summary="Liveness (always 200)")
async def health(request: Request) -> HealthResponse:
    """Report that the API process is up, its version, and the database state."""
    db = await check_database(_engine(request))
    return HealthResponse(
        status="ok",
        version=__version__,
        database="ok" if db.available else "unavailable",
        revision=db.revision,
    )


@router.get("/ready", response_model=ReadyResponse, summary="Readiness (503 if DB is down)")
async def ready(request: Request) -> ReadyResponse:
    """200 only when the database answers; otherwise 503 in the standard error shape."""
    db = await check_database(_engine(request))
    if not db.available:
        raise ServiceUnavailableError("Database unavailable")
    return ReadyResponse(status="ready", revision=db.revision)
