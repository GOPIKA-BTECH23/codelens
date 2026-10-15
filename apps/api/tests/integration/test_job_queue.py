"""Job queue behaviour against real Postgres."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.code import Job as JobRow
from app.db.models.enums import JobStatus
from app.services.jobs import Job, PostgresJobQueue

Factory = async_sessionmaker[AsyncSession]


async def _enqueue(
    queue: PostgresJobQueue, factory: Factory, type: str = "review", **kwargs: object
) -> None:
    async with factory.begin() as session:
        await queue.enqueue(session, type, {"n": 1}, **kwargs)  # type: ignore[arg-type]


async def _row(factory: Factory, job: Job) -> JobRow:
    async with factory() as session:
        return (await session.execute(select(JobRow).where(JobRow.id == job.id))).scalar_one()


async def _db_now(factory: Factory) -> datetime:
    async with factory() as session:
        return await session.scalar(select(func.now()))  # type: ignore[return-value]


async def _make_stale(factory: Factory, job: Job, age: timedelta = timedelta(hours=1)) -> None:
    async with factory.begin() as session:
        await session.execute(
            update(JobRow).where(JobRow.id == job.id).values(locked_at=func.now() - age)
        )


async def _make_due(factory: Factory, job: Job) -> None:
    async with factory.begin() as session:
        await session.execute(
            update(JobRow)
            .where(JobRow.id == job.id)
            .values(run_after=func.now() - timedelta(seconds=1))
        )


@pytest.fixture
def queue(session_factory: Factory) -> PostgresJobQueue:
    return PostgresJobQueue(
        session_factory, visibility_timeout=timedelta(minutes=5), backoff_base=timedelta(seconds=10)
    )


# --- claiming -----------------------------------------------------------------------------


async def test_claim_returns_a_running_job_and_nothing_else(
    queue: PostgresJobQueue, session_factory: Factory
) -> None:
    await _enqueue(queue, session_factory)

    job = await queue.claim_next()

    assert job is not None
    assert (job.type, job.payload, job.attempts) == ("review", {"n": 1}, 1)
    row = await _row(session_factory, job)
    assert row.status == JobStatus.RUNNING and row.locked_at is not None
    assert await queue.claim_next() is None


async def test_claim_on_an_empty_queue_returns_none(queue: PostgresJobQueue) -> None:
    assert await queue.claim_next() is None


async def test_jobs_scheduled_in_the_future_are_not_claimed(
    queue: PostgresJobQueue, session_factory: Factory
) -> None:
    await _enqueue(queue, session_factory, run_after=datetime.now(UTC) + timedelta(hours=1))

    assert await queue.claim_next() is None


async def test_oldest_run_after_is_claimed_first(
    queue: PostgresJobQueue, session_factory: Factory
) -> None:
    now = datetime.now(UTC)
    await _enqueue(queue, session_factory, "late", run_after=now - timedelta(minutes=1))
    await _enqueue(queue, session_factory, "early", run_after=now - timedelta(minutes=10))

    first = await queue.claim_next()

    assert first is not None and first.type == "early"


async def test_enqueue_joins_the_callers_transaction(
    queue: PostgresJobQueue, session_factory: Factory
) -> None:
    async with session_factory() as session:
        await queue.enqueue(session, "review", {})
        await session.rollback()

    assert await queue.claim_next() is None


async def test_a_row_locked_by_someone_else_is_skipped_not_waited_for(
    queue: PostgresJobQueue, session_factory: Factory
) -> None:
    """Deterministic SKIP LOCKED check: hold the only job's row lock in another transaction."""
    await _enqueue(queue, session_factory)

    async with session_factory.begin() as holder:
        await holder.execute(select(JobRow).with_for_update())  # lock held until the block ends
        # If SKIP LOCKED were missing, this would block until the holder commits (timeout).
        claimed = await asyncio.wait_for(queue.claim_next(), timeout=5)

    assert claimed is None
    assert await queue.claim_next() is not None  # lock released: now it can be claimed


async def test_concurrent_claimers_never_get_the_same_job(
    queue: PostgresJobQueue, session_factory: Factory
) -> None:
    total, workers = 60, 8
    for _ in range(total):
        await _enqueue(queue, session_factory)

    async def worker() -> list[Job]:
        got: list[Job] = []
        while (job := await queue.claim_next()) is not None:
            got.append(job)
            await asyncio.sleep(0)  # let the other workers interleave
        return got

    results = await asyncio.gather(*(worker() for _ in range(workers)))

    ids = [job.id for claimed in results for job in claimed]
    assert len(ids) == total, "every job claimed"
    assert len(set(ids)) == total, "no job claimed twice"
    assert sum(1 for claimed in results if claimed) > 1, "work was actually shared"


async def test_two_callers_racing_for_one_job_exactly_one_wins(
    queue: PostgresJobQueue, session_factory: Factory
) -> None:
    await _enqueue(queue, session_factory)

    results = await asyncio.gather(queue.claim_next(), queue.claim_next())

    assert sorted(r is None for r in results) == [False, True]


# --- completing and failing ---------------------------------------------------------------


async def test_mark_succeeded(queue: PostgresJobQueue, session_factory: Factory) -> None:
    await _enqueue(queue, session_factory)
    job = await queue.claim_next()
    assert job is not None

    assert await queue.mark_succeeded(job) is True

    row = await _row(session_factory, job)
    assert row.status == JobStatus.SUCCEEDED and row.locked_at is None
    assert await queue.claim_next() is None


async def test_failed_job_is_retried_with_exponential_backoff_then_goes_dead(
    session_factory: Factory,
) -> None:
    queue = PostgresJobQueue(session_factory, backoff_base=timedelta(seconds=10))
    await _enqueue(queue, session_factory, max_attempts=3)

    expected_delays = [10, 20]  # attempt 1 -> 10s, attempt 2 -> 20s, attempt 3 -> dead
    for attempt, delay in enumerate(expected_delays, start=1):
        job = await queue.claim_next()
        assert job is not None and job.attempts == attempt

        assert await queue.mark_failed(job, f"boom {attempt}") == JobStatus.QUEUED

        row = await _row(session_factory, job)
        wait = (row.run_after - await _db_now(session_factory)).total_seconds()
        assert delay - 2 < wait <= delay, f"attempt {attempt}: waits ~{delay}s, got {wait:.1f}s"
        assert row.last_error == f"boom {attempt}"
        assert await queue.claim_next() is None, "must not run again before the backoff passes"
        await _make_due(session_factory, job)

    last = await queue.claim_next()
    assert last is not None and last.attempts == 3
    assert await queue.mark_failed(last, "boom 3") == JobStatus.DEAD
    row = await _row(session_factory, last)
    assert row.status == JobStatus.DEAD and row.last_error == "boom 3"
    await _make_due(session_factory, last)
    assert await queue.claim_next() is None, "a dead job is never claimed again"


async def test_backoff_doubles_then_is_capped(session_factory: Factory) -> None:
    queue = PostgresJobQueue(
        session_factory, backoff_base=timedelta(seconds=30), backoff_max=timedelta(minutes=2)
    )

    delays = [queue._backoff(attempt) for attempt in range(1, 7)]

    assert [d.total_seconds() for d in delays] == [30, 60, 120, 120, 120, 120]


async def test_long_error_messages_are_truncated(
    queue: PostgresJobQueue, session_factory: Factory
) -> None:
    await _enqueue(queue, session_factory)
    job = await queue.claim_next()
    assert job is not None

    await queue.mark_failed(job, "e" * 10_000)

    assert len((await _row(session_factory, job)).last_error or "") == 2000


# --- stale locks --------------------------------------------------------------------------


async def test_stale_lock_is_recovered_and_the_job_runs_again(
    queue: PostgresJobQueue, session_factory: Factory
) -> None:
    await _enqueue(queue, session_factory)
    job = await queue.claim_next()
    assert job is not None
    await _make_stale(session_factory, job)

    assert await queue.recover_stale_locks() == 1

    row = await _row(session_factory, job)
    assert row.status == JobStatus.QUEUED and row.locked_at is None
    again = await queue.claim_next()
    assert again is not None and again.id == job.id and again.attempts == 2


async def test_fresh_locks_are_left_alone(
    queue: PostgresJobQueue, session_factory: Factory
) -> None:
    await _enqueue(queue, session_factory)
    job = await queue.claim_next()
    assert job is not None
    await _make_stale(session_factory, job, age=timedelta(minutes=1))  # < 5 min timeout

    assert await queue.recover_stale_locks() == 0
    assert (await _row(session_factory, job)).status == JobStatus.RUNNING


async def test_stale_job_that_used_all_attempts_goes_dead(
    queue: PostgresJobQueue, session_factory: Factory
) -> None:
    await _enqueue(queue, session_factory, max_attempts=1)
    job = await queue.claim_next()
    assert job is not None
    await _make_stale(session_factory, job)

    assert await queue.recover_stale_locks() == 1

    assert (await _row(session_factory, job)).status == JobStatus.DEAD


async def test_a_worker_that_lost_its_lock_cannot_overwrite_the_new_owner(
    queue: PostgresJobQueue, session_factory: Factory
) -> None:
    await _enqueue(queue, session_factory)
    slow = await queue.claim_next()
    assert slow is not None
    await _make_stale(session_factory, slow)
    await queue.recover_stale_locks()
    fast = await queue.claim_next()
    assert fast is not None and fast.attempts == 2

    assert await queue.mark_succeeded(slow) is False
    assert await queue.mark_failed(slow, "late") is None

    assert (await _row(session_factory, fast)).status == JobStatus.RUNNING
    assert await queue.mark_succeeded(fast) is True


# --- timestamps ---------------------------------------------------------------------------


async def test_updated_at_moves_on_claim_and_on_completion(
    queue: PostgresJobQueue, session_factory: Factory
) -> None:
    await _enqueue(queue, session_factory)
    async with session_factory() as session:
        created = (await session.execute(select(JobRow))).scalar_one()
        seen = [created.updated_at]
        job_id = created.id

    async def current() -> datetime:
        async with session_factory() as s:
            return (
                await s.execute(select(JobRow.updated_at).where(JobRow.id == job_id))
            ).scalar_one()

    await asyncio.sleep(0.05)
    job = await queue.claim_next()  # a Core UPDATE via the ORM unit of work
    assert job is not None
    seen.append(await current())
    await asyncio.sleep(0.05)
    await queue.mark_succeeded(job)  # a bulk Core update() statement
    seen.append(await current())

    assert seen[0] < seen[1] < seen[2]
    assert (await _row(session_factory, job)).created_at == created.created_at, (
        "created_at is fixed"
    )


async def test_updated_at_moves_on_orm_attribute_updates(session_factory: Factory) -> None:
    async with session_factory.begin() as session:
        session.add(JobRow(type="x", payload={}))
    async with session_factory() as session:
        row = (await session.execute(select(JobRow))).scalar_one()
        before = row.updated_at
        await asyncio.sleep(0.05)
        row.last_error = "changed"
        await session.commit()
        await session.refresh(row)

        assert row.updated_at > before


async def test_raw_sql_updates_do_not_touch_updated_at(session_factory: Factory) -> None:
    """Documents the limit of the onupdate approach (see TimestampMixin): hand-written SQL
    bypasses it, so application code must use SQLAlchemy statements."""
    async with session_factory.begin() as session:
        session.add(JobRow(type="x", payload={}))
    async with session_factory.begin() as session:
        before = await session.scalar(select(JobRow.updated_at))
        await asyncio.sleep(0.05)
        await session.execute(text("UPDATE jobs SET last_error = 'raw'"))
    async with session_factory() as session:
        assert await session.scalar(select(JobRow.updated_at)) == before
