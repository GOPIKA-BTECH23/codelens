"""Async engine, session factory and the per-request session dependency."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the engine. It is lazy: no connection is made until the first query."""
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # Test each pooled connection before use, so a restarted database does not surface
        # as one failed request.
        pool_pre_ping=True,
        connect_args={"timeout": 5},  # asyncpg connect timeout, seconds
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory. expire_on_commit=False so objects stay readable after commit
    (async sessions cannot lazy-load attributes the way sync sessions can)."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, the ONLY place that commits or rolls back.

    Services and route handlers never call commit(); they flush at most. If the handler
    raises, everything is rolled back. Declare it with `Depends(get_session, scope="function")`
    (see SessionDep) so the commit happens BEFORE the response is sent. Otherwise a failed
    commit would arrive after the client already saw a success.
    """
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
