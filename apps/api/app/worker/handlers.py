"""Job handlers and the registry that maps a job type to its handler.

Handlers must be IDEMPOTENT. The queue is at-least-once: if a worker dies after doing the work
but before marking the job done, or a job outlives the visibility timeout, the same job runs
again. So every state change here is a guarded transition ("queued/running -> running"), never
a blind write, and running a job twice must leave the same result as running it once.

A handler signals the outcome like this: return = success; raise PermanentJobError = never
retry; raise anything else = transient, retry with backoff.

`purge_installation` is deliberately NOT registered (ADR 0004). It is a data-deletion
obligation, so until its real handler exists the worker marks those jobs `dead` ("no handler
registered"), which is visible, instead of silently reporting the data as purged.
"""

import logging
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.enums import RunStatus
from app.services import cost_guard, runs
from app.services.jobs import Job
from app.services.webhooks import JOB_INDEX_REPO, JOB_REVIEW_PR
from app.worker.errors import PermanentJobError

logger = logging.getLogger("app.worker.handlers")

# Stored in review_runs.model so a stub "success" can never be mistaken for a real review.
STUB_MODEL = "m4-stub-no-review"


@dataclass(frozen=True)
class HandlerContext:
    """Dependencies handed to every handler (no globals, so tests can swap them)."""

    session_factory: async_sessionmaker[AsyncSession]
    daily_cost_cap_usd: Decimal


Handler = Callable[[Job, HandlerContext], Coroutine[Any, Any, None]]


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


# --- review_pr --------------------------------------------------------------------------------


async def review_pr(job: Job, ctx: HandlerContext) -> None:
    """Run a review. M4: the run goes queued -> running -> succeeded and NO REVIEW HAPPENS.

    This wrapper keeps review_runs consistent with the job: a transient error puts the run
    back to `queued` (the job will be retried), the final failed attempt or a permanent error
    marks it `failed`. The real pipeline replaces `_perform_review` in M5.
    """
    try:
        run_id = uuid.UUID(str(job.payload["review_run_id"]))
    except (KeyError, ValueError) as exc:
        raise PermanentJobError("job payload has no valid review_run_id") from exc

    try:
        await _perform_review(ctx, run_id)
    except PermanentJobError as exc:
        await _fail_run(ctx, run_id, str(exc))
        raise
    except Exception as exc:
        if job.attempts >= job.max_attempts:
            await _fail_run(
                ctx, run_id, f"gave up after {job.attempts} attempts: {_error_text(exc)}"
            )
        else:
            message = f"attempt {job.attempts} failed, will retry: {_error_text(exc)}"
            async with ctx.session_factory.begin() as session:
                await runs.requeue_for_retry(session, run_id, message)
        raise


async def _fail_run(ctx: HandlerContext, run_id: uuid.UUID, message: str) -> None:
    async with ctx.session_factory.begin() as session:
        await runs.mark_failed(session, run_id, message)


async def _perform_review(ctx: HandlerContext, run_id: uuid.UUID) -> None:
    """Decide whether to run, then run (stub). Kept separate so tests can make it fail."""
    async with ctx.session_factory.begin() as session:
        info = await runs.load_run(session, run_id)
        if info is None:
            raise PermanentJobError(f"review run {run_id} does not exist")
        if info.status == RunStatus.SUPERSEDED:
            logger.info("run was superseded by a newer push; exiting early")
            return
        if info.status in runs.TERMINAL:
            # A redelivered job whose work already finished: nothing to do (idempotency).
            logger.info("run already finished; nothing to do", extra={"status": info.status})
            return
        if info.installation_uninstalled:
            raise PermanentJobError("installation was uninstalled; nothing to review")
        spent = await cost_guard.cap_reached(session, ctx.daily_cost_cap_usd)
        if spent is not None:
            reason = f"daily cost cap reached (${spent:.2f} of ${ctx.daily_cost_cap_usd:.2f})"
            await runs.mark_skipped(session, run_id, reason)
            logger.warning("review skipped: %s", reason)
            return
        await runs.mark_running(session, run_id)  # committed here, so observers see `running`
    logger.info("review run started")

    # M5 replaces everything between this line and mark_succeeded with the LangGraph pipeline.
    logger.warning("review_pr is a stub: NO REVIEW WAS PERFORMED (the real pipeline arrives in M5)")

    async with ctx.session_factory.begin() as session:
        if await runs.mark_succeeded(session, run_id, model=STUB_MODEL):
            logger.info("review run finished (stub, no review performed)")
        else:
            # A webhook superseded the run while we worked; its result must be dropped.
            logger.info("run changed state while running (superseded?); result dropped")


# --- index_repo -------------------------------------------------------------------------------


async def index_repo(job: Job, ctx: HandlerContext) -> None:
    """Stub: the indexer arrives in M8. Succeeds so push webhooks do not pile up as dead jobs."""
    logger.warning(
        "index_repo is not implemented yet (M8): nothing was indexed",
        extra={"repository_id": job.payload.get("repository_id")},
    )


HANDLERS: dict[str, Handler] = {
    JOB_REVIEW_PR: review_pr,
    JOB_INDEX_REPO: index_repo,
}
