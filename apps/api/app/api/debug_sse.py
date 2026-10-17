"""TEMPORARY (M7): a stream to test whether SSE survives the Vercel rewrite (ADR 0002).

Only mounted when DEBUG_SSE_ENABLED=true. It sends one event per second carrying the
server's clock; a client that receives them one by one (not all at the end) has proven the
path does not buffer. Remove this file, its router line in main.py and the setting once the
result is in the ADR.
"""

import asyncio
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(tags=["debug"])


async def _events(count: int, interval: float) -> AsyncIterator[str]:
    for i in range(count):
        yield f"data: {i} {time.time():.3f}\n\n"
        await asyncio.sleep(interval)
    yield "event: done\ndata: end\n\n"


@router.get("/debug/sse", include_in_schema=False)
async def debug_sse(count: int = 10, interval: float = 1.0) -> StreamingResponse:
    count = max(1, min(count, 60))
    interval = max(0.1, min(interval, 5.0))
    return StreamingResponse(
        _events(count, interval),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no"},  # ask nginx-style proxies not to buffer
    )
