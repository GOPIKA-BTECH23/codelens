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
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import UsageTotals
from app.ai.schemas import Finding
from app.db.models.accounts import Installation
from app.db.models.enums import RunStatus
from app.db.models.repos import Finding as FindingRow
from app.db.models.repos import PullRequest, Repository, ReviewRun, ReviewRunFile
from app.services.diff import FileDiff, render_patch

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


@dataclass(frozen=True)
class ReviewTarget:
    """Which PR a run reviews, and through which installation (for the GitHub token)."""

    repo_full_name: str
    pr_number: int
    github_installation_id: int
    settings: dict[str, Any]  # repositories.settings (M6); read with repos.effective_settings


async def load_review_target(session: AsyncSession, run_id: uuid.UUID) -> ReviewTarget | None:
    row = (
        await session.execute(
            select(
                Repository.full_name,
                PullRequest.number,
                Installation.github_installation_id,
                Repository.settings,
            )
            .select_from(ReviewRun)  # the join chain starts at the run
            .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
            .join(Repository, Repository.id == PullRequest.repository_id)
            .join(Installation, Installation.id == Repository.installation_id)
            .where(ReviewRun.id == run_id)
        )
    ).one_or_none()
    if row is None:
        return None
    return ReviewTarget(row.full_name, row.number, row.github_installation_id, row.settings)


async def add_usage(
    session: AsyncSession,
    run_id: uuid.UUID,
    totals: UsageTotals,
    *,
    model: str,
    prompt_version: str | None,
) -> None:
    """ADD this attempt's LLM usage to the run's totals (any status).

    Added, not overwritten: if an attempt spends tokens and then fails, the retry spends
    them again, and both were (notionally) billed. Overwriting would hide real spend from
    the daily cost cap.
    """
    await session.execute(
        update(ReviewRun)
        .where(ReviewRun.id == run_id)
        .values(
            tokens_in=func.coalesce(ReviewRun.tokens_in, 0) + totals.tokens_in,
            tokens_out=func.coalesce(ReviewRun.tokens_out, 0) + totals.tokens_out,
            cost_usd=func.coalesce(ReviewRun.cost_usd, 0) + totals.cost_usd,
            model=model,
            prompt_version=prompt_version,
        )
    )


async def replace_findings(
    session: AsyncSession,
    run_id: uuid.UUID,
    head_sha: str,
    findings: Sequence[Finding],
    *,
    summary: dict[str, Any] | None = None,
    files: Sequence[FileDiff] = (),
) -> None:
    """Store the findings about to be posted (replacing any from an earlier attempt that
    never got posted), the commit they were computed for, and what the dashboard needs to
    show them: the summary and the reviewed files' patches (replaced the same way)."""
    await session.execute(delete(FindingRow).where(FindingRow.review_run_id == run_id))
    await session.execute(delete(ReviewRunFile).where(ReviewRunFile.review_run_id == run_id))
    session.add_all(
        ReviewRunFile(
            review_run_id=run_id,
            path=f.path,
            old_path=f.old_path,
            status=f.status.value,
            additions=f.additions,
            deletions=f.deletions,
            patch=render_patch(f) if f.hunks else None,
        )
        for f in files
    )
    session.add_all(
        FindingRow(
            review_run_id=run_id,
            file=f.file,
            line=f.line,
            end_line=f.end_line,
            severity=f.severity.value,
            category=f.category,
            message=f.message,
            suggestion=f.suggestion,
            confidence=f.confidence,
        )
        for f in findings
    )
    await session.execute(
        update(ReviewRun).where(ReviewRun.id == run_id).values(head_sha=head_sha, summary=summary)
    )
    await session.flush()


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
    """running -> succeeded. `results` sets columns such as latency_ms and github_review_id."""
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
