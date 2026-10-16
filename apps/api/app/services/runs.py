"""The review_runs state machine.

    queued --> running --> succeeded
       |          |  \\---> failed
       |          |  \\---> skipped      (deliberately not run, e.g. daily cost cap)
       |          |  \\---> superseded   (a newer push made it stale; set by the webhook)
       |          \\------> queued       (transient error, the job queue will retry it)
       \\--> failed | skipped | superseded

`succeeded`, `failed`, `skipped` and `superseded` are terminal. Every transition is one
`UPDATE ... WHERE status IN (<allowed sources>)`, so a worker holding stale information (say,
it finished a run that a webhook superseded meanwhile) cannot overwrite the newer state: the
UPDATE matches no row and the function returns False. These functions flush but never commit;
the caller owns the transaction.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.accounts import Installation
from app.db.models.enums import RunStatus
from app.db.models.repos import PullRequest, Repository, ReviewRun

MAX_ERROR_LENGTH = 2000

Q = RunStatus.QUEUED
R = RunStatus.RUNNING
OK = RunStatus.SUCCEEDED
FAILED = RunStatus.FAILED
SUPERSEDED = RunStatus.SUPERSEDED
SKIPPED = RunStatus.SKIPPED

# target status -> statuses it may be entered from. The single source of truth for the diagram
# above (a unit test pins it). running -> running is allowed so a redelivered job is harmless.
ALLOWED_SOURCES: dict[RunStatus, frozenset[RunStatus]] = {
    R: frozenset({Q, R}),
    OK: frozenset({R}),
    FAILED: frozenset({Q, R}),
    SKIPPED: frozenset({Q, R}),
    SUPERSEDED: frozenset({Q, R}),
    Q: frozenset({Q, R}),  # retry after a transient error (also fails before it ever ran)
}
TERMINAL = frozenset({OK, FAILED, SKIPPED, SUPERSEDED})


@dataclass(frozen=True)
class RunInfo:
    """What a handler needs to decide whether to proceed."""

    id: uuid.UUID
    status: RunStatus
    installation_uninstalled: bool


async def load_run(session: AsyncSession, run_id: uuid.UUID) -> RunInfo | None:
    """The run's status plus whether its installation has been uninstalled (None if no run)."""
    row = (
        await session.execute(
            select(ReviewRun.id, ReviewRun.status, Installation.uninstalled_at)
            .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
            .join(Repository, Repository.id == PullRequest.repository_id)
            .join(Installation, Installation.id == Repository.installation_id)
            .where(ReviewRun.id == run_id)
        )
    ).one_or_none()
    if row is None:
        return None
    return RunInfo(
        id=row.id,
        status=RunStatus(row.status),
        installation_uninstalled=row.uninstalled_at is not None,
    )


async def _transition(
    session: AsyncSession, run_id: uuid.UUID, to: RunStatus, **values: Any
) -> bool:
    result = await session.execute(
        update(ReviewRun)
        .where(ReviewRun.id == run_id, ReviewRun.status.in_(ALLOWED_SOURCES[to]))
        .values(status=to, **values)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def mark_running(session: AsyncSession, run_id: uuid.UUID) -> bool:
    """queued/running -> running. started_at keeps the FIRST start across retries."""
    return await _transition(
        session,
        run_id,
        R,
        started_at=func.coalesce(ReviewRun.started_at, func.now()),
        finished_at=None,
        error=None,
    )


async def mark_succeeded(session: AsyncSession, run_id: uuid.UUID, **results: Any) -> bool:
    """running -> succeeded. `results` carries model/tokens/cost/latency once M5 has them."""
    return await _transition(session, run_id, OK, finished_at=func.now(), error=None, **results)


async def mark_failed(session: AsyncSession, run_id: uuid.UUID, error: str) -> bool:
    """queued/running -> failed (permanent failure, or the last retry was used up)."""
    return await _transition(
        session, run_id, FAILED, finished_at=func.now(), error=error[:MAX_ERROR_LENGTH]
    )


async def mark_skipped(session: AsyncSession, run_id: uuid.UUID, reason: str) -> bool:
    """queued/running -> skipped. The reason is stored in `error` so the UI can show it."""
    return await _transition(
        session, run_id, SKIPPED, finished_at=func.now(), error=reason[:MAX_ERROR_LENGTH]
    )


async def requeue_for_retry(session: AsyncSession, run_id: uuid.UUID, error: str) -> bool:
    """running (or still queued) -> queued after a transient error; the error stays visible."""
    return await _transition(session, run_id, Q, error=error[:MAX_ERROR_LENGTH])
