"""Fixed-window rate limiting behind an interface (CLAUDE.md decision 22).

Fixed window: time is cut into windows of `window_seconds`; each key may make `limit`
requests per window. Simple and cheap, at the cost of allowing up to 2x the limit across a
window boundary. That is fine for brute-force and abuse protection on /auth/*.

The Postgres implementation commits each hit in its OWN short transaction, so a request
that is then rejected (or rolled back) still counts.
"""

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import case, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import RateLimitCounter

# Roughly 1 hit in 100 also deletes counters that have been idle for an hour, so the
# table stays as small as the set of recently active clients.
_PURGE_PROBABILITY = 0.01
_PURGE_AFTER = timedelta(hours=1)


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int  # 0 when allowed


class RateLimiter(Protocol):
    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        """Count one request for `key` and say whether it is within the limit."""
        ...


class PostgresRateLimiter:
    """One row per key in rate_limit_counters, updated with a single atomic upsert."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def hit(
        self, key: str, *, limit: int, window_seconds: int, now: datetime | None = None
    ) -> RateLimitResult:
        now = now or datetime.now(UTC)
        epoch = int(now.timestamp())
        window_start = datetime.fromtimestamp(epoch - epoch % window_seconds, tz=UTC)

        # INSERT ... ON CONFLICT DO UPDATE is atomic: two concurrent requests for the same
        # key can never both read the old count. A new window resets the count to 1.
        base = insert(RateLimitCounter).values(key=key, window_start=window_start, count=1)
        same_window = RateLimitCounter.window_start == base.excluded.window_start
        stmt = base.on_conflict_do_update(
            index_elements=[RateLimitCounter.key],
            set_={
                "count": case((same_window, RateLimitCounter.count + 1), else_=1),
                "window_start": base.excluded.window_start,
            },
        ).returning(RateLimitCounter.count)

        async with self._session_factory() as session, session.begin():
            count = (await session.execute(stmt)).scalar_one()
            if random.random() < _PURGE_PROBABILITY:
                await self.purge_stale(session, now)

        if count <= limit:
            return RateLimitResult(allowed=True, retry_after_seconds=0)
        window_end = window_start + timedelta(seconds=window_seconds)
        return RateLimitResult(
            allowed=False, retry_after_seconds=max(1, int((window_end - now).total_seconds()))
        )

    @staticmethod
    async def purge_stale(session: AsyncSession, now: datetime) -> None:
        """Delete counters whose window started more than an hour ago."""
        await session.execute(
            delete(RateLimitCounter).where(RateLimitCounter.window_start < now - _PURGE_AFTER)
        )
