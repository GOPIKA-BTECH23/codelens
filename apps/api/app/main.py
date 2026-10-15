"""FastAPI application factory.

Run with: uvicorn --factory app.main:create_app
A factory (instead of a module-level `app`) means settings are only loaded when the server
starts, and tests can build an app with explicit settings.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import auth, health, installations, me
from app.config import Settings, get_settings
from app.core.csrf import CSRF_HEADER, CsrfMiddleware
from app.core.errors import register_error_handlers
from app.core.headers import SecurityHeadersMiddleware
from app.core.logging import configure_logging
from app.core.middleware import REQUEST_ID_HEADER, RequestIdMiddleware
from app.core.rate_limit import PostgresRateLimiter
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
    app.state.rate_limiter = PostgresRateLimiter(app.state.session_factory)

    # Middleware: the LAST one added runs FIRST. Request order, outermost first:
    #   RequestId -> SecurityHeaders -> CORS -> CSRF -> routes
    # so every response, including CSRF and CORS rejections, gets a request ID and the
    # security headers.
    app.add_middleware(CsrfMiddleware, web_origin=settings.web_origin)
    # The browser normally talks to us same-origin through the /api proxy, where CORS does
    # not apply. This locks down anyone calling the API origin directly from a browser.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", CSRF_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
        max_age=600,
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.environment == "production")
    app.add_middleware(RequestIdMiddleware)

    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(me.router)
    app.include_router(installations.router)

    logging.getLogger("app").info("api started", extra={"version": __version__})
    return app
