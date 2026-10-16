"""Job queue: an interface plus a Postgres implementation (SELECT ... FOR UPDATE SKIP LOCKED).

Life of a job:
    enqueue -> queued --claim_next--> running --mark_succeeded--> succeeded
                 ^                      |
                 |   mark_failed        +--mark_failed / stale lock--> queued (after backoff)
                 +----------------------+                         \\-> dead (attempts used up)

Design rules:
  * Claiming is a SHORT transaction: lock one row, set status='running', COMMIT. The handler
    then runs with no transaction and no row lock held, so a 2-minute LLM call never keeps a
    database transaction open. The `running` status + `locked_at` is the "lease".
  * A worker that dies mid-job leaves a `running` row behind. `recover_stale_locks` returns
    such rows to `queued` once `locked_at` is older than the visibility timeout.
  * `attempts` is incremented at CLAIM time, so a job that keeps crashing its worker still
    runs out of attempts and ends up `dead` instead of looping forever.
  * All time comparisons use the database clock (now()), never the worker's, so workers on
    machines with skewed clocks agree.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.code import Job as JobRow
from app.db.models.enums import JobStatus

MAX_ERROR_LENGTH = 2000


@dataclass(frozen=True)
class Job:
    """A claimed job, detached from the database session (safe to use after commit)."""

    id: uuid.UUID
    type: str
    payload: Mapping[str, Any]
    attempts: int  # includes the current attempt
    max_attempts: int


class JobQueue(Protocol):
    """What the rest of the app depends on. A Redis queue could implement this later."""

    async def enqueue(
        self,
        session: AsyncSession,
        type: str,
        payload: Mapping[str, Any],
        run_after: datetime | None = None,
        max_attempts: int = 5,
    ) -> uuid.UUID: ...

    async def claim_next(self) -> Job | None: ...

    async def mark_succeeded(self, job: Job) -> bool: ...

    async def mark_failed(self, job: Job, error: str) -> JobStatus | None: ...

    async def recover_stale_locks(self) -> int: ...


class PostgresJobQueue:
    """JobQueue backed by the `jobs` table.

    Args:
        session_factory: used for claim/complete/fail/recover, each in its own short transaction.
        visibility_timeout: how long a job may stay `running` before it is presumed abandoned.
            It must be longer than the slowest legitimate handler.
        backoff_base / backoff_max: retry delay is base * 2^(attempt-1), capped at max.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        visibility_timeout: timedelta = timedelta(minutes=5),
        backoff_base: timedelta = timedelta(seconds=30),
        backoff_max: timedelta = timedelta(hours=1),
    ) -> None:
        self._session_factory = session_factory
        self._visibility_timeout = visibility_timeout
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max

    async def enqueue(
        self,
        session: AsyncSession,
        type: str,
        payload: Mapping[str, Any],
        run_after: datetime | None = None,
        max_attempts: int = 5,
    ) -> uuid.UUID:
        """Insert a job using the CALLER's session and transaction (no commit here).

        That is deliberate: the webhook handler records the delivery, creates the review run
        and enqueues the job in ONE transaction, so we can never have a delivery marked
        "seen" without its job (or the reverse). A Redis queue could not join that
        transaction, which is a real argument for keeping the queue in Postgres.
        """
        job = JobRow(type=type, payload=dict(payload), max_attempts=max_attempts)
        if run_after is not None:
            job.run_after = run_after
        session.add(job)
        await session.flush()
        return job.id

    async def claim_next(self) -> Job | None:
        """Claim the oldest runnable job, or return None.

        FOR UPDATE locks the chosen row; SKIP LOCKED makes other workers pass over rows that
        are locked right now instead of waiting. So two workers never get the same job and
        never block each other. The transaction commits as soon as the row is marked running.
        """
        async with self._session_factory.begin() as session:
            row = (
                await session.execute(
                    select(JobRow)
                    .where(JobRow.status == JobStatus.QUEUED, JobRow.run_after <= func.now())
                    .order_by(JobRow.run_after, JobRow.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            row.status = JobStatus.RUNNING
            row.locked_at = func.now()
            row.attempts += 1
            await session.flush()
            # Re-read what the database wrote (updated_at/locked_at were server-side values).
            await session.refresh(row)
            return Job(
                id=row.id,
                type=row.type,
                payload=row.payload,
                attempts=row.attempts,
                max_attempts=row.max_attempts,
            )

    async def mark_succeeded(self, job: Job) -> bool:
        """Mark a running job done. False if we no longer own it (its lock was recovered)."""
        async with self._session_factory.begin() as session:
            result = await session.execute(
                update(JobRow)
                .where(self._owned(job))
                .values(status=JobStatus.SUCCEEDED, locked_at=None, last_error=None)
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def mark_failed(self, job: Job, error: str) -> JobStatus | None:
        """Record a failure: back to `queued` after an exponential backoff, or `dead` once
        max_attempts is reached. Returns the new status, or None if we no longer own the job.
        """
        error = error[:MAX_ERROR_LENGTH]
        if job.attempts >= job.max_attempts:
            new_status = JobStatus.DEAD
            values: dict[str, Any] = {"status": new_status, "locked_at": None, "last_error": error}
        else:
            new_status = JobStatus.QUEUED
            values = {
                "status": new_status,
                "locked_at": None,
                "last_error": error,
                "run_after": func.now() + self._backoff(job.attempts),
            }
        async with self._session_factory.begin() as session:
            result = await session.execute(update(JobRow).where(self._owned(job)).values(**values))
            return new_status if result.rowcount else None  # type: ignore[attr-defined]

    async def recover_stale_locks(self) -> int:
        """Release `running` jobs whose lock is older than the visibility timeout.

        They go back to `queued` (run immediately), or `dead` if they already used all their
        attempts. Returns how many jobs were recovered.
        """
        reason = "worker lost: visibility timeout expired"
        stale = and_(
            JobRow.status == JobStatus.RUNNING,
            JobRow.locked_at < func.now() - self._visibility_timeout,
        )
        async with self._session_factory.begin() as session:
            dead = await session.execute(
                update(JobRow)
                .where(stale, JobRow.attempts >= JobRow.max_attempts)
                .values(status=JobStatus.DEAD, locked_at=None, last_error=reason)
            )
            requeued = await session.execute(
                update(JobRow)
                .where(stale)  # the dead ones above are no longer `running`
                .values(status=JobStatus.QUEUED, locked_at=None, last_error=reason)
            )
            return int(dead.rowcount) + int(requeued.rowcount)  # type: ignore[attr-defined]

    def _backoff(self, attempts: int) -> timedelta:
        """base * 2^(attempts-1): 30s, 60s, 120s, ... capped at backoff_max."""
        delay: timedelta = self._backoff_base * 2 ** (attempts - 1)
        return min(delay, self._backoff_max)

    @staticmethod
    def _owned(job: Job) -> Any:
        """WHERE clause: this exact claim. The attempts check stops a slow worker whose lock
        was recovered (and the job re-claimed by someone else) from overwriting the new
        owner's result."""
        return and_(
            JobRow.id == job.id,
            JobRow.status == JobStatus.RUNNING,
            JobRow.attempts == job.attempts,
        )
