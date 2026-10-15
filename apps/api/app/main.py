"""FastAPI application factory.

Run with: uvicorn --factory app.main:create_app
A factory (instead of a module-level `app`) means settings are only loaded when the server
starts, and tests can build an app with explicit settings.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api import health
from app.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware
from app.db.session import create_engine, create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Raises pydantic.ValidationError at startup if required config is missing."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    # The engine is lazy (no connection yet), so building the app never needs a live database.
    engine = create_engine(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()  # close pooled connections on shutdown

    app = FastAPI(title="CodeLens API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)

    logging.getLogger("app").info("api started", extra={"version": __version__})
    return app
