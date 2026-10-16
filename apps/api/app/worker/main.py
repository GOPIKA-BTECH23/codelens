"""The worker: claims jobs from the Postgres queue and dispatches them to handlers.

Two ways to run it (ADR 0004):
  * its own process: `python -m app.worker.main` (docker-compose `worker` service, local dev)
  * inside the API process: RUN_WORKER_IN_API=true (free-tier demos), see app/main.py

Both call `Worker.run(stop_event)`. Setting the event is a graceful shutdown: no new jobs are
claimed, running jobs get `shutdown_grace_seconds` to finish, and any still running are
cancelled and RELEASED back to the queue (no attempt used). A hard kill (SIGKILL, power loss)
is covered by the queue's visibility timeout instead: `recover_stale_locks` re-queues the job.
"""

import asyncio
import contextlib
import logging
import random
import signal
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.llm.factory import build_llm, model_name
from app.config import Settings, get_settings
from app.core.logging import configure_logging, job_id_var, run_id_var
from app.db.session import create_engine, create_session_factory
from app.services.github_app import GitHubApp, load_private_key
from app.services.jobs import Job, JobQueue, PostgresJobQueue
from app.worker.errors import PermanentJobError
from app.worker.handlers import HANDLERS, Handler, HandlerContext
from app.worker.review import (
    GitHubReviewPipeline,
    ReviewConfig,
    ReviewPipeline,
    installation_clients,
)

logger = logging.getLogger("app.worker")

RECOVERY_INTERVAL_SECONDS = 30.0
JITTER = 0.5  # idle sleeps are poll_seconds * uniform(1 - JITTER, 1 + JITTER)


class _Released:
    """Sentinel: shutdown interrupted the handler, so the job must be given back."""


RELEASED = _Released()


class Worker:
    """N concurrent claim-and-run loops plus one stale-lock recovery loop."""

    def __init__(
        self,
        queue: JobQueue,
        handlers: dict[str, Handler],
        ctx: HandlerContext,
        *,
        concurrency: int = 2,
        poll_seconds: float = 2.0,
        shutdown_grace_seconds: float = 20.0,
        recovery_interval_seconds: float = RECOVERY_INTERVAL_SECONDS,
    ) -> None:
        self._queue = queue
        self._handlers = handlers
        self._ctx = ctx
        self._concurrency = concurrency
        self._poll_seconds = poll_seconds
        self._grace = shutdown_grace_seconds
        self._recovery_interval = recovery_interval_seconds

    async def run(self, stop: asyncio.Event) -> None:
        """Work until `stop` is set, then drain gracefully."""
        logger.info(
            "worker started",
            extra={"concurrency": self._concurrency, "job_types": sorted(self._handlers)},
        )
        loops = [self._recovery_loop(stop)]
        loops += [self._slot_loop(stop) for _ in range(self._concurrency)]
        await asyncio.gather(*loops)
        logger.info("worker stopped")

    # --- loops ----------------------------------------------------------------------------

    async def _slot_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                job = await self._queue.claim_next()
            except Exception:
                # Database down or restarting: stay alive and try again after a pause.
                logger.exception("claiming a job failed")
                await self._sleep(stop, self._poll_seconds * 5)
                continue
            if job is None:
                await self._sleep(stop, self._poll_seconds)
                continue
            try:
                await self._process(job, stop)
            except Exception:
                # Bookkeeping failed (e.g. the DB vanished while marking the job). The job
                # stays `running` and the visibility timeout will recover it.
                logger.exception("job bookkeeping failed", extra={"job_type": job.type})
            finally:
                job_id_var.set(None)
                run_id_var.set(None)

    async def _recovery_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                recovered = await self._queue.recover_stale_locks()
                if recovered:
                    logger.warning("recovered stale jobs", extra={"count": recovered})
            except Exception:
                logger.exception("stale-lock recovery failed")
            await self._sleep(stop, self._recovery_interval, jitter=False)

    @staticmethod
    async def _sleep(stop: asyncio.Event, seconds: float, *, jitter: bool = True) -> None:
        """Sleep, but wake at once on shutdown. Jitter stops several workers polling in step."""
        if jitter:
            seconds *= random.uniform(1 - JITTER, 1 + JITTER)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=seconds)

    # --- one job --------------------------------------------------------------------------

    async def _process(self, job: Job, stop: asyncio.Event) -> None:
        job_id_var.set(str(job.id))
        run_id = job.payload.get("review_run_id")
        run_id_var.set(str(run_id) if run_id else None)
        extra = {"job_type": job.type, "attempt": job.attempts, "max_attempts": job.max_attempts}

        handler = self._handlers.get(job.type)
        if handler is None:
            message = f"no handler registered for job type '{job.type}'"
            logger.error("%s; marking the job dead", message, extra=extra)
            await self._queue.mark_dead(job, message)
            return

        logger.info("job started", extra=extra)
        outcome = await self._run_handler(handler, job, stop)
        if isinstance(outcome, _Released):
            await self._queue.release(job)
            logger.warning("shutdown: job released back to the queue", extra=extra)
        elif outcome is None:
            if await self._queue.mark_succeeded(job):
                logger.info("job succeeded", extra=extra)
            else:
                logger.warning("job finished but its lock was lost; result ignored", extra=extra)
        elif isinstance(outcome, PermanentJobError):
            await self._queue.mark_dead(job, str(outcome))
            logger.error("job failed permanently, not retrying: %s", outcome, extra=extra)
        else:
            new_status = await self._queue.mark_failed(job, f"{type(outcome).__name__}: {outcome}")
            logger.warning(
                "job failed (%s)",
                new_status,
                extra=extra,
                exc_info=(type(outcome), outcome, outcome.__traceback__),
            )

    async def _run_handler(
        self, handler: Handler, job: Job, stop: asyncio.Event
    ) -> BaseException | _Released | None:
        """Run the handler. Returns None (success), the exception it raised, or RELEASED when
        shutdown interrupted it. The handler runs as a task so shutdown can wait on both."""
        handler_task = asyncio.create_task(handler(job, self._ctx))
        stop_task = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait({handler_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            if not handler_task.done():
                logger.info("shutdown requested; waiting for the running job")
                await asyncio.wait({handler_task}, timeout=self._grace)
            if not handler_task.done():
                handler_task.cancel()
                await asyncio.gather(handler_task, return_exceptions=True)
                return RELEASED
            return handler_task.exception()  # None on success
        finally:
            stop_task.cancel()


class WorkerConfigError(RuntimeError):
    """The worker cannot start with this configuration (message never contains secrets)."""


def review_config(settings: Settings) -> ReviewConfig:
    return ReviewConfig(
        confidence_threshold=settings.review_confidence_threshold,
        max_comments=settings.review_max_comments,
        ignore_patterns=settings.review_ignore_patterns,
        max_file_changed_lines=settings.review_max_file_changed_lines,
        no_code_behavior=settings.review_no_code_behavior,
        chunk_max_tokens=settings.review_chunk_max_tokens,
        llm_concurrency=settings.review_llm_concurrency,
    )


@contextlib.asynccontextmanager
async def review_resources(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[ReviewPipeline]:
    """Build what the review pipeline needs and close it all on exit.

    Fails at startup (not at the first review) when the GitHub App key or the LLM key is
    missing, or the model has no price.
    """
    if settings.github_app_private_key is None:
        raise WorkerConfigError(
            "GITHUB_APP_PRIVATE_KEY is required to run the worker (it posts reviews as the App)"
        )
    private_key = load_private_key(settings.github_app_private_key.get_secret_value())
    llm = build_llm(settings)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
            app = GitHubApp(
                http, client_id=settings.github_oauth_client_id, private_key=private_key
            )
            logger.info(
                "review pipeline ready",
                extra={"llm_provider": llm.name, "llm_model": model_name(settings)},
            )
            yield GitHubReviewPipeline(
                session_factory, llm, review_config(settings), installation_clients(app, http)
            )
    finally:
        await llm.aclose()


def build_worker(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    queue: JobQueue | None = None,
    *,
    review_pipeline: ReviewPipeline | None = None,
) -> Worker:
    """Wire a Worker from settings (shared by the standalone process and the API lifespan)."""
    queue = queue or PostgresJobQueue(
        session_factory,
        visibility_timeout=timedelta(seconds=settings.job_visibility_timeout_seconds),
    )
    ctx = HandlerContext(
        session_factory=session_factory,
        daily_cost_cap_usd=settings.daily_cost_cap_usd,
        review_pipeline=review_pipeline,
    )
    return Worker(
        queue,
        HANDLERS,
        ctx,
        concurrency=settings.worker_concurrency,
        poll_seconds=settings.worker_poll_seconds,
        shutdown_grace_seconds=settings.worker_shutdown_grace_seconds,
    )


async def main() -> None:
    """Entry point of `python -m app.worker.main`."""
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows event loops have no add_signal_handler; plain signal handlers run in the
            # main thread, so hop back onto the loop safely.
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        async with review_resources(settings, session_factory) as pipeline:
            worker = build_worker(settings, session_factory, review_pipeline=pipeline)
            await worker.run(stop)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
