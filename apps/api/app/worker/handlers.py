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

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Repository
from app.db.models.enums import IndexStatus, RunStatus
from app.services import cost_guard, runs
from app.services.jobs import Job
from app.services.webhooks import JOB_INDEX_REPO, JOB_REVIEW_PR
from app.worker.errors import PermanentJobError
from app.worker.review import ReviewPipeline

logger = logging.getLogger("app.worker.handlers")


@dataclass(frozen=True)
class HandlerContext:
    """Dependencies handed to every handler (no globals, so tests can swap them)."""

    session_factory: async_sessionmaker[AsyncSession]
    daily_cost_cap_usd: Decimal
    # The M5 review pipeline (worker/review.py). None only in tests that never reach it.
    review_pipeline: ReviewPipeline | None = None


Handler = Callable[[Job, HandlerContext], Coroutine[Any, Any, None]]


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


# --- review_pr --------------------------------------------------------------------------------


async def review_pr(job: Job, ctx: HandlerContext) -> None:
    """Run a review: gate checks here, then the pipeline in worker/review.py.

    This wrapper keeps review_runs consistent with the job: a transient error puts the run
    back to `queued` (the job will be retried), the final failed attempt or a permanent error
    marks it `failed`.
    """
    try:
        run_id = uuid.UUID(str(job.payload["review_run_id"]))
    except (KeyError, ValueError) as exc:
        raise PermanentJobError("job payload has no valid review_run_id") from exc

    head_sha = job.payload.get("head_sha")
    try:
        await _perform_review(ctx, run_id, head_sha if isinstance(head_sha, str) else None)
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


async def _perform_review(ctx: HandlerContext, run_id: uuid.UUID, head_sha: str | None) -> None:
    """Decide whether to run, then run the pipeline. Kept separate so tests can make it fail."""
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
        if ctx.review_pipeline is None:
            raise PermanentJobError("no review pipeline configured in this worker")
        await runs.mark_running(session, run_id)  # committed here, so observers see `running`
    logger.info("review run started")
    # The pipeline ends the run itself (succeeded / skipped) or raises (see the wrapper).
    await ctx.review_pipeline(run_id, head_sha)


# --- index_repo -------------------------------------------------------------------------------


async def index_repo(job: Job, ctx: HandlerContext) -> None:
    """Stub until M8: fails VISIBLY instead of pretending to index. The repository goes to
    `failed` (a push webhook or the dashboard had set it to `queued`, which would otherwise
    never change) and the job goes `dead` with the reason in `last_error`."""
    raw = job.payload.get("repository_id")
    try:
        repository_id = uuid.UUID(str(raw))
    except ValueError:
        repository_id = None
    if repository_id is not None:
        async with ctx.session_factory.begin() as session:
            await session.execute(
                update(Repository)
                .where(Repository.id == repository_id)
                .values(index_status=IndexStatus.FAILED)
            )
    raise PermanentJobError("indexing is not available yet (arrives in M8); nothing was indexed")


HANDLERS: dict[str, Handler] = {
    JOB_REVIEW_PR: review_pr,
    JOB_INDEX_REPO: index_repo,
}
