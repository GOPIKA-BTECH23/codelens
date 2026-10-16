"""Client-side request limiter (CLAUDE.md decision 10).

Free tiers allow a fixed number of requests per minute. Without a limiter, a big PR split into
many chunks would fire them all at once, get HTTP 429s, and burn retries. This keeps us under
the limit instead: at most `per_minute` calls START in any 60-second window (a sliding log of
start times). It is per process, which matches our deployment (one worker process); several
workers would each need a share of the limit.
"""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable

WINDOW_SECONDS = 60.0


class RequestRateLimiter:
    def __init__(
        self,
        per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if per_minute < 1:
            raise ValueError("per_minute must be at least 1")
        self._per_minute = per_minute
        self._clock = clock
        self._sleep = sleep
        self._starts: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until another request may start, then record it. Waiters go in FIFO order
        because they queue on the lock."""
        async with self._lock:
            while True:
                now = self._clock()
                while self._starts and now - self._starts[0] >= WINDOW_SECONDS:
                    self._starts.popleft()
                if len(self._starts) < self._per_minute:
                    self._starts.append(now)
                    return
                await self._sleep(WINDOW_SECONDS - (now - self._starts[0]))
