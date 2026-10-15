"""Liveness/readiness endpoint used by Docker, Render, the uptime pinger and the web app."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    # "not_configured" until M1 adds the database layer; then "ok" or "error".
    database: Literal["ok", "error", "not_configured"]


@router.get("/health", response_model=HealthResponse, summary="Service health")
async def health() -> HealthResponse:
    """Report that the API process is up, its version, and whether the database is reachable."""
    return HealthResponse(status="ok", version=__version__, database="not_configured")
