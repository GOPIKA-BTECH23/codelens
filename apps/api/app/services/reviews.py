"""Pull request and review queries for the dashboard, and finding feedback."""

import logging
import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams
from app.db.models import Finding, PullRequest, ReviewRun, ReviewRunFile
from app.db.models.enums import PullRequestState
from app.schemas.dashboard import (
    DiffLineOut,
    FindingOut,
    FindingUpdate,
    HunkOut,
    PullOut,
    ReviewFileOut,
    ReviewOut,
    RunOut,
    RunSummary,
    SummaryOut,
)
from app.services.diff import DiffParseError, parse_hunks

logger = logging.getLogger("app.services.reviews")


def _pull_out(pull: PullRequest, run: RunSummary | None) -> PullOut:
    return PullOut(
        id=pull.id,
        repository_id=pull.repository_id,
        number=pull.number,
        title=pull.title,
        author_login=pull.author_login,
        state=PullRequestState(pull.state),
        created_at=pull.created_at,
        updated_at=pull.updated_at,
        latest_run=run,
    )


async def _run_summaries(
    db: AsyncSession, pull_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, RunSummary]:
    """Latest run per PR, with the number of findings that are still open."""
    if not pull_ids:
        return {}
    latest = (
        select(ReviewRun)
        .where(ReviewRun.pull_request_id.in_(pull_ids))
        .ext(distinct_on(ReviewRun.pull_request_id))
        .order_by(ReviewRun.pull_request_id, ReviewRun.created_at.desc())
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                latest.c.pull_request_id,
                latest.c.id,
                latest.c.status,
                latest.c.created_at,
                func.count(Finding.id).filter(Finding.dismissed.is_(False)),
            )
            .select_from(latest)
            .outerjoin(Finding, Finding.review_run_id == latest.c.id)
            .group_by(latest.c.pull_request_id, latest.c.id, latest.c.status, latest.c.created_at)
        )
    ).all()
    return {
        r[0]: RunSummary(id=r[1], status=r[2], created_at=r[3], findings_count=r[4]) for r in rows
    }


async def list_pulls(
    db: AsyncSession,
    repository_id: uuid.UUID,
    page: PageParams,
    *,
    state: PullRequestState | None = None,
) -> Page[PullOut]:
    """PRs of one repository, most recently updated first."""
    base = select(PullRequest).where(PullRequest.repository_id == repository_id)
    if state is not None:
        base = base.where(PullRequest.state == state.value)
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    pulls = (
        await db.scalars(
            base.order_by(PullRequest.updated_at.desc(), PullRequest.number.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    summaries = await _run_summaries(db, [p.id for p in pulls])
    items = [_pull_out(p, summaries.get(p.id)) for p in pulls]
    return Page[PullOut](items=items, total=total, limit=page.limit, offset=page.offset)


async def pull_out(db: AsyncSession, pull: PullRequest) -> PullOut:
    return _pull_out(pull, (await _run_summaries(db, [pull.id])).get(pull.id))


def finding_out(f: Finding) -> FindingOut:
    return FindingOut(
        id=f.id,
        file=f.file,
        line=f.line,
        end_line=f.end_line,
        severity=f.severity,  # type: ignore[arg-type]  # CHECK constraint guarantees the enum
        category=f.category,
        message=f.message,
        suggestion=f.suggestion,
        confidence=f.confidence,
        user_feedback=f.user_feedback,  # type: ignore[arg-type]
        dismissed=f.dismissed,
    )


def _file_out(f: ReviewRunFile) -> ReviewFileOut:
    hunks: list[HunkOut] = []
    available = f.patch is not None
    if f.patch:
        try:
            hunks = [
                HunkOut(
                    header=h.header,
                    lines=[
                        DiffLineOut(
                            kind=ln.kind, old_line=ln.old_line, new_line=ln.new_line, text=ln.text
                        )
                        for ln in h.lines
                    ],
                )
                for h in parse_hunks(f.patch)
            ]
        except DiffParseError:
            logger.warning("stored patch could not be parsed", extra={"file_id": str(f.id)})
            available = False
    return ReviewFileOut(
        path=f.path,
        old_path=f.old_path,
        status=f.status,
        additions=f.additions,
        deletions=f.deletions,
        hunks=hunks,
        patch_available=available,
    )


async def latest_review(db: AsyncSession, pull_id: uuid.UUID) -> ReviewOut | None:
    """The PR's most recent run with its findings, summary and reviewed files (None: the
    PR has not been reviewed yet)."""
    run = await db.scalar(
        select(ReviewRun)
        .where(ReviewRun.pull_request_id == pull_id)
        .order_by(ReviewRun.created_at.desc())
        .limit(1)
    )
    if run is None:
        return None
    findings = (
        await db.scalars(
            select(Finding)
            .where(Finding.review_run_id == run.id)
            .order_by(Finding.file, Finding.line, Finding.created_at)
        )
    ).all()
    files = (
        await db.scalars(
            select(ReviewRunFile)
            .where(ReviewRunFile.review_run_id == run.id)
            .order_by(ReviewRunFile.path)
        )
    ).all()
    return ReviewOut(
        run=RunOut(
            id=run.id,
            status=run.status,  # type: ignore[arg-type]
            model=run.model,
            prompt_version=run.prompt_version,
            tokens_in=run.tokens_in,
            tokens_out=run.tokens_out,
            cost_usd=float(run.cost_usd) if run.cost_usd is not None else None,
            latency_ms=run.latency_ms,
            head_sha=run.head_sha,
            error=run.error,
            created_at=run.created_at,
            finished_at=run.finished_at,
        ),
        summary=SummaryOut.model_validate(run.summary) if run.summary else None,
        findings=[finding_out(f) for f in findings],
        files=[_file_out(f) for f in files],
    )


async def update_finding(db: AsyncSession, finding: Finding, change: FindingUpdate) -> FindingOut:
    """Apply feedback and/or dismissal. A field that was not sent is left alone; an explicit
    `user_feedback: null` clears the vote."""
    sent = change.model_fields_set
    if "user_feedback" in sent:
        finding.user_feedback = change.user_feedback.value if change.user_feedback else None
    if change.dismissed is not None:
        finding.dismissed = change.dismissed
    await db.flush()
    return finding_out(finding)
