"""The worker against real Postgres: claiming, retries, shutdown and the review_runs states.

These tests are about the queue and the review_pr WRAPPER, so they plug in a stand-in
pipeline (StandInPipeline) instead of the real one. The real pipeline, with a mocked GitHub
and the FakeLLM, is tested in test_review_pipeline.py.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from decimal import Decimal
from typing import Literal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import job_id_var
from app.db.models.accounts import Installation
from app.db.models.code import Job as JobRow
from app.db.models.enums import JobStatus, RunStatus
from app.db.models.repos import PullRequest, Repository, ReviewRun
from app.services import runs as runs_service
from app.services.jobs import Job, PostgresJobQueue
from app.services.webhooks import JOB_INDEX_REPO, JOB_PURGE_INSTALLATION, JOB_REVIEW_PR
from app.worker import handlers as handlers_module
from app.worker.errors import PermanentJobError
from app.worker.handlers import HANDLERS, Handler, HandlerContext
from app.worker.main import Worker
from app.worker.review import ReviewPipeline

Factory = async_sessionmaker[AsyncSession]


# --- helpers ------------------------------------------------------------------------------

STAND_IN_MODEL = "test-stand-in-pipeline"


class StandInPipeline:
    """Does what the real pipeline does to the run on success, and records each call."""

    def __init__(self, factory: Factory) -> None:
        self.factory = factory
        self.calls: list[tuple[uuid.UUID, str | None, str]] = []  # (run, head_sha, run status)

    async def __call__(self, run_id: uuid.UUID, head_sha: str | None) -> None:
        self.calls.append((run_id, head_sha, (await get_run(self.factory, run_id)).status))
        async with self.factory.begin() as session:
            await runs_service.mark_succeeded(session, run_id, model=STAND_IN_MODEL)


@pytest.fixture
def queue(session_factory: Factory) -> PostgresJobQueue:
    # Tiny backoff so retry tests do not wait: delays are 20 ms, 40 ms, ...
    return PostgresJobQueue(
        session_factory,
        visibility_timeout=timedelta(minutes=5),
        backoff_base=timedelta(milliseconds=20),
    )


def make_worker(
    factory: Factory,
    queue: PostgresJobQueue,
    handlers: dict[str, Handler] | None = None,
    *,
    cap: Decimal = Decimal("5"),
    pipeline: ReviewPipeline | Literal["none"] | None = None,
    **kwargs: float,
) -> Worker:
    ctx = HandlerContext(
        session_factory=factory,
        daily_cost_cap_usd=cap,
        review_pipeline=(None if pipeline == "none" else pipeline or StandInPipeline(factory)),
    )
    settings: dict[str, float] = {
        "concurrency": 2,
        "poll_seconds": 0.02,
        "shutdown_grace_seconds": 1.0,
        "recovery_interval_seconds": 0.05,
    }
    settings.update(kwargs)
    return Worker(
        queue,
        HANDLERS if handlers is None else handlers,
        ctx,
        concurrency=int(settings["concurrency"]),
        poll_seconds=settings["poll_seconds"],
        shutdown_grace_seconds=settings["shutdown_grace_seconds"],
        recovery_interval_seconds=settings["recovery_interval_seconds"],
    )


async def wait_for(condition: Callable[[], Awaitable[bool]], timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not await condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


@contextlib.asynccontextmanager
async def running(worker: Worker) -> AsyncIterator[asyncio.Event]:
    """Run a worker in the background; on exit shut it down gracefully."""
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    try:
        yield stop
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=10)


async def enqueue(
    queue: PostgresJobQueue,
    factory: Factory,
    type: str,
    payload: dict[str, object] | None = None,
    max_attempts: int = 5,
) -> uuid.UUID:
    async with factory.begin() as session:
        return await queue.enqueue(session, type, payload or {}, max_attempts=max_attempts)


async def job_row(factory: Factory, job_id: uuid.UUID) -> JobRow:
    async with factory() as session:
        return (await session.execute(select(JobRow).where(JobRow.id == job_id))).scalar_one()


async def job_status(factory: Factory, job_id: uuid.UUID) -> str:
    return (await job_row(factory, job_id)).status


def job_is(
    factory: Factory, job_id: uuid.UUID, *statuses: JobStatus
) -> Callable[[], Awaitable[bool]]:
    async def check() -> bool:
        return await job_status(factory, job_id) in statuses

    return check


async def make_run(
    factory: Factory,
    *,
    uninstalled: bool = False,
    status: RunStatus = RunStatus.QUEUED,
    number: int = 1,
) -> uuid.UUID:
    async with factory.begin() as session:
        inst = Installation(
            github_installation_id=number,
            account_login=f"acct{number}",
            account_type="user",
            uninstalled_at=func.now() if uninstalled else None,
        )
        session.add(inst)
        await session.flush()
        repo = Repository(installation_id=inst.id, github_repo_id=number, full_name=f"o/r{number}")
        session.add(repo)
        await session.flush()
        pr = PullRequest(repository_id=repo.id, number=1, title="t", author_login="a")
        session.add(pr)
        await session.flush()
        run = ReviewRun(pull_request_id=pr.id, status=status)
        session.add(run)
        await session.flush()
        return run.id


async def get_run(factory: Factory, run_id: uuid.UUID) -> ReviewRun:
    async with factory() as session:
        return (await session.execute(select(ReviewRun).where(ReviewRun.id == run_id))).scalar_one()


def run_is(factory: Factory, run_id: uuid.UUID, status: RunStatus) -> Callable[[], Awaitable[bool]]:
    async def check() -> bool:
        return (await get_run(factory, run_id)).status == status

    return check


async def enqueue_review(
    queue: PostgresJobQueue, factory: Factory, run_id: uuid.UUID, **kw: int
) -> uuid.UUID:
    return await enqueue(queue, factory, JOB_REVIEW_PR, {"review_run_id": str(run_id)}, **kw)


# --- queue semantics ----------------------------------------------------------------------


async def test_two_workers_process_every_job_exactly_once(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    seen: list[uuid.UUID] = []

    async def handler(job: Job, ctx: HandlerContext) -> None:
        await asyncio.sleep(0.01)  # long enough for the workers to overlap
        seen.append(job.id)

    ids = [await enqueue(queue, session_factory, "t") for _ in range(20)]
    w1 = make_worker(session_factory, queue, {"t": handler})
    w2 = make_worker(session_factory, queue, {"t": handler})
    async with running(w1), running(w2):

        async def all_done() -> bool:
            return all([await job_status(session_factory, i) == "succeeded" for i in ids])

        await wait_for(all_done)

    assert sorted(seen) == sorted(ids)  # each job handled once, none twice, none lost


async def test_failing_job_is_retried_with_backoff_then_goes_dead(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    calls: list[int] = []

    async def handler(job: Job, ctx: HandlerContext) -> None:
        calls.append(job.attempts)
        raise RuntimeError("boom")

    job_id = await enqueue(queue, session_factory, "t", max_attempts=3)
    async with running(make_worker(session_factory, queue, {"t": handler})):
        await wait_for(job_is(session_factory, job_id, JobStatus.DEAD))

    assert calls == [1, 2, 3]  # retried until attempts ran out, then stopped
    row = await job_row(session_factory, job_id)
    assert row.last_error is not None and "RuntimeError: boom" in row.last_error


async def test_permanent_error_is_not_retried(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    calls: list[int] = []

    async def handler(job: Job, ctx: HandlerContext) -> None:
        calls.append(job.attempts)
        raise PermanentJobError("repository was deleted")

    job_id = await enqueue(queue, session_factory, "t", max_attempts=5)
    async with running(make_worker(session_factory, queue, {"t": handler})):
        await wait_for(job_is(session_factory, job_id, JobStatus.DEAD))

    assert calls == [1]
    assert (await job_row(session_factory, job_id)).last_error == "repository was deleted"


async def test_job_type_without_a_handler_is_dead_not_succeeded(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    job_id = await enqueue(queue, session_factory, "mystery")
    async with running(make_worker(session_factory, queue, {})):
        await wait_for(job_is(session_factory, job_id, JobStatus.DEAD))

    row = await job_row(session_factory, job_id)
    assert row.attempts == 1  # not retried
    assert row.last_error == "no handler registered for job type 'mystery'"


async def test_purge_installation_is_not_registered_so_it_cannot_look_done(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    """ADR 0004: the uninstall purge is a data-deletion duty. Until its handler exists the job
    must end up dead and visible, never succeeded."""
    assert JOB_PURGE_INSTALLATION not in HANDLERS
    job_id = await enqueue(queue, session_factory, JOB_PURGE_INSTALLATION, {"installation_id": "x"})
    async with running(make_worker(session_factory, queue)):
        await wait_for(job_is(session_factory, job_id, JobStatus.DEAD))

    assert "no handler registered" in (await job_row(session_factory, job_id)).last_error  # type: ignore[operator]


async def test_index_repo_stub_logs_not_implemented_and_succeeds(
    session_factory: Factory, queue: PostgresJobQueue, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    job_id = await enqueue(queue, session_factory, JOB_INDEX_REPO, {"repository_id": "r"})
    async with running(make_worker(session_factory, queue)):
        await wait_for(job_is(session_factory, job_id, JobStatus.SUCCEEDED))

    assert "index_repo is not implemented" in caplog.text


async def test_handler_sees_the_job_id_for_log_correlation(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    seen: list[str | None] = []

    async def handler(job: Job, ctx: HandlerContext) -> None:
        seen.append(job_id_var.get())

    job_id = await enqueue(queue, session_factory, "t")
    async with running(make_worker(session_factory, queue, {"t": handler})):
        await wait_for(job_is(session_factory, job_id, JobStatus.SUCCEEDED))

    assert seen == [str(job_id)]


# --- shutdown and crashes -----------------------------------------------------------------


async def test_sigterm_mid_job_releases_it_and_another_worker_finishes_it(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    started = asyncio.Event()

    async def hangs(job: Job, ctx: HandlerContext) -> None:
        started.set()
        await asyncio.Event().wait()  # never finishes on its own

    job_id = await enqueue(queue, session_factory, "t")
    worker = make_worker(session_factory, queue, {"t": hangs}, shutdown_grace_seconds=0.1)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    await asyncio.wait_for(started.wait(), timeout=5)

    stop.set()  # what the SIGTERM handler does
    await asyncio.wait_for(task, timeout=5)

    row = await job_row(session_factory, job_id)
    assert row.status == JobStatus.QUEUED  # recoverable, not stuck in `running`
    assert row.attempts == 0  # the interrupted attempt was refunded
    assert row.locked_at is None

    finished: list[uuid.UUID] = []

    async def quick(job: Job, ctx: HandlerContext) -> None:
        finished.append(job.id)

    async with running(make_worker(session_factory, queue, {"t": quick})):
        await wait_for(job_is(session_factory, job_id, JobStatus.SUCCEEDED))
    assert finished == [job_id]


async def test_sigterm_lets_a_job_finish_if_it_fits_in_the_grace_period(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    started = asyncio.Event()

    async def slowish(job: Job, ctx: HandlerContext) -> None:
        started.set()
        await asyncio.sleep(0.2)

    job_id = await enqueue(queue, session_factory, "t")
    worker = make_worker(session_factory, queue, {"t": slowish}, shutdown_grace_seconds=5)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    await asyncio.wait_for(started.wait(), timeout=5)
    stop.set()
    await asyncio.wait_for(task, timeout=5)

    assert await job_status(session_factory, job_id) == JobStatus.SUCCEEDED


async def test_job_of_a_crashed_worker_is_recovered_and_run_again(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    """A SIGKILLed worker leaves a `running` row. The visibility timeout gives it back."""
    job_id = await enqueue(queue, session_factory, "t")
    claimed = await queue.claim_next()  # a worker claims it ... and "dies"
    assert claimed is not None
    async with session_factory.begin() as session:
        await session.execute(
            update(JobRow)
            .where(JobRow.id == job_id)
            .values(locked_at=func.now() - timedelta(hours=1))
        )

    handled: list[int] = []

    async def handler(job: Job, ctx: HandlerContext) -> None:
        handled.append(job.attempts)

    async with running(make_worker(session_factory, queue, {"t": handler})):
        await wait_for(job_is(session_factory, job_id, JobStatus.SUCCEEDED))
    assert handled == [2]  # second attempt


# --- review_pr and the review_runs states ----------------------------------------------------


async def test_handler_hands_the_running_run_and_its_head_sha_to_the_pipeline(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    pipeline = StandInPipeline(session_factory)
    run_id = await make_run(session_factory)
    job_id = await enqueue(
        queue, session_factory, JOB_REVIEW_PR, {"review_run_id": str(run_id), "head_sha": "c0ffee"}
    )
    async with running(make_worker(session_factory, queue, pipeline=pipeline)):
        await wait_for(job_is(session_factory, job_id, JobStatus.SUCCEEDED))

    # The pipeline only ever sees a `running` run, with the commit the webhook recorded.
    assert pipeline.calls == [(run_id, "c0ffee", RunStatus.RUNNING)]
    run = await get_run(session_factory, run_id)
    assert run.status == RunStatus.SUCCEEDED
    assert run.started_at is not None and run.finished_at is not None


async def test_review_job_fails_permanently_without_a_pipeline(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    run_id = await make_run(session_factory)
    job_id = await enqueue_review(queue, session_factory, run_id)
    async with running(make_worker(session_factory, queue, pipeline="none")):
        await wait_for(job_is(session_factory, job_id, JobStatus.DEAD))
    assert (await get_run(session_factory, run_id)).status == RunStatus.FAILED


async def test_superseded_run_exits_early_without_touching_the_run(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    run_id = await make_run(session_factory, status=RunStatus.SUPERSEDED)
    job_id = await enqueue_review(queue, session_factory, run_id)
    async with running(make_worker(session_factory, queue)):
        await wait_for(job_is(session_factory, job_id, JobStatus.SUCCEEDED))

    run = await get_run(session_factory, run_id)
    assert run.status == RunStatus.SUPERSEDED
    assert run.started_at is None and run.model is None  # nothing ran


async def test_redelivered_job_for_a_finished_run_changes_nothing(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    """At-least-once delivery: the same job may run twice. The second run must be a no-op."""
    run_id = await make_run(session_factory)
    async with running(make_worker(session_factory, queue)):
        first = await enqueue_review(queue, session_factory, run_id)
        await wait_for(job_is(session_factory, first, JobStatus.SUCCEEDED))
        before = await get_run(session_factory, run_id)
        second = await enqueue_review(queue, session_factory, run_id)  # the "duplicate"
        await wait_for(job_is(session_factory, second, JobStatus.SUCCEEDED))

    after = await get_run(session_factory, run_id)
    assert (after.status, after.started_at, after.finished_at) == (
        before.status,
        before.started_at,
        before.finished_at,
    )


async def test_uninstalled_installation_fails_the_run_permanently(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    run_id = await make_run(session_factory, uninstalled=True)
    job_id = await enqueue_review(queue, session_factory, run_id, max_attempts=5)
    async with running(make_worker(session_factory, queue)):
        await wait_for(job_is(session_factory, job_id, JobStatus.DEAD))

    assert (await job_row(session_factory, job_id)).attempts == 1  # no retries
    run = await get_run(session_factory, run_id)
    assert run.status == RunStatus.FAILED
    assert run.error is not None and "uninstalled" in run.error


async def test_missing_run_is_a_permanent_failure(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    job_id = await enqueue_review(queue, session_factory, uuid.uuid4())
    async with running(make_worker(session_factory, queue)):
        await wait_for(job_is(session_factory, job_id, JobStatus.DEAD))
    assert (await job_row(session_factory, job_id)).attempts == 1


async def test_transient_error_requeues_the_run_and_the_retry_succeeds(
    session_factory: Factory, queue: PostgresJobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = handlers_module._perform_review
    seen_between: list[tuple[str, str | None]] = []

    async def flaky(ctx: HandlerContext, run_id: uuid.UUID, head_sha: str | None) -> None:
        run = await get_run(session_factory, run_id)
        if not seen_between and run.error is None:
            seen_between.append(("first", None))
            raise ConnectionError("github is down")
        seen_between.append((run.status, run.error))  # state the retry starts from
        await real(ctx, run_id, head_sha)

    monkeypatch.setattr(handlers_module, "_perform_review", flaky)
    run_id = await make_run(session_factory)
    job_id = await enqueue_review(queue, session_factory, run_id)
    async with running(make_worker(session_factory, queue)):
        await wait_for(run_is(session_factory, run_id, RunStatus.SUCCEEDED))

    assert seen_between[1][0] == RunStatus.QUEUED  # between attempts the run waited as `queued`
    assert "github is down" in (seen_between[1][1] or "")  # ... with the error visible
    assert (await job_row(session_factory, job_id)).attempts == 2
    assert (await get_run(session_factory, run_id)).error is None  # cleared by the success


async def test_run_is_failed_when_the_last_attempt_fails(
    session_factory: Factory, queue: PostgresJobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def always_fails(ctx: HandlerContext, run_id: uuid.UUID, head_sha: str | None) -> None:
        raise ConnectionError("github is down")

    monkeypatch.setattr(handlers_module, "_perform_review", always_fails)
    run_id = await make_run(session_factory)
    job_id = await enqueue_review(queue, session_factory, run_id, max_attempts=2)
    async with running(make_worker(session_factory, queue)):
        await wait_for(job_is(session_factory, job_id, JobStatus.DEAD))

    run = await get_run(session_factory, run_id)
    assert run.status == RunStatus.FAILED
    assert run.error is not None and "gave up after 2 attempts" in run.error


# --- daily cost cap -----------------------------------------------------------------------


async def _spend_today(factory: Factory, amount: str, *, days_ago: int = 0) -> None:
    other = await make_run(factory, number=99)
    async with factory.begin() as session:
        await session.execute(
            update(ReviewRun)
            .where(ReviewRun.id == other)
            .values(
                status=RunStatus.SUCCEEDED,
                cost_usd=Decimal(amount),
                started_at=func.now() - timedelta(days=days_ago),
            )
        )


async def test_run_is_skipped_with_a_reason_when_the_daily_cap_is_reached(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    await _spend_today(session_factory, "5.50")
    run_id = await make_run(session_factory, number=1)
    job_id = await enqueue_review(queue, session_factory, run_id)
    async with running(make_worker(session_factory, queue, cap=Decimal("5"))):
        await wait_for(job_is(session_factory, job_id, JobStatus.SUCCEEDED))

    run = await get_run(session_factory, run_id)
    assert run.status == RunStatus.SKIPPED
    assert run.error is not None and "daily cost cap reached" in run.error
    assert run.model is None  # the pipeline never ran


async def test_run_proceeds_when_spend_is_under_the_cap(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    await _spend_today(session_factory, "1.00")
    run_id = await make_run(session_factory, number=1)
    job_id = await enqueue_review(queue, session_factory, run_id)
    async with running(make_worker(session_factory, queue, cap=Decimal("5"))):
        await wait_for(job_is(session_factory, job_id, JobStatus.SUCCEEDED))
    assert (await get_run(session_factory, run_id)).status == RunStatus.SUCCEEDED


async def test_yesterdays_spend_does_not_count(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    await _spend_today(session_factory, "50.00", days_ago=2)
    run_id = await make_run(session_factory, number=1)
    job_id = await enqueue_review(queue, session_factory, run_id)
    async with running(make_worker(session_factory, queue, cap=Decimal("5"))):
        await wait_for(job_is(session_factory, job_id, JobStatus.SUCCEEDED))
    assert (await get_run(session_factory, run_id)).status == RunStatus.SUCCEEDED


async def test_a_cap_of_zero_skips_everything(
    session_factory: Factory, queue: PostgresJobQueue
) -> None:
    run_id = await make_run(session_factory)
    job_id = await enqueue_review(queue, session_factory, run_id)
    async with running(make_worker(session_factory, queue, cap=Decimal("0"))):
        await wait_for(job_is(session_factory, job_id, JobStatus.SUCCEEDED))
    assert (await get_run(session_factory, run_id)).status == RunStatus.SKIPPED


# --- state machine guards -----------------------------------------------------------------


async def test_state_machine_rejects_illegal_transitions(session_factory: Factory) -> None:
    from app.services import runs

    run_id = await make_run(session_factory, status=RunStatus.SUPERSEDED)
    async with session_factory.begin() as session:
        # A worker that finishes late must not resurrect a superseded run.
        assert not await runs.mark_succeeded(session, run_id)
        assert not await runs.mark_running(session, run_id)
        assert not await runs.mark_failed(session, run_id, "x")
    assert (await get_run(session_factory, run_id)).status == RunStatus.SUPERSEDED

    done = await make_run(session_factory, status=RunStatus.SUCCEEDED, number=2)
    async with session_factory.begin() as session:
        assert not await runs.mark_running(session, done)  # succeeded -> running is illegal
        assert not await runs.requeue_for_retry(session, done, "x")
