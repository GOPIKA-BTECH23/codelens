"""FastAPI application factory.

Run with: uvicorn --factory app.main:create_app
A factory (instead of a module-level `app`) means settings are only loaded when the server
starts, and tests can build an app with explicit settings.
"""

import logging

from fastapi import FastAPI

from app import __version__
from app.api import health
from app.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Raises pydantic.ValidationError at startup if required config is missing."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="CodeLens API", version=__version__)
    app.state.settings = settings
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)

    logging.getLogger("app").info("api started", extra={"version": __version__})
    return app
