"""Usage analytics for admins: runs, estimated cost, latency, acceptance rate per day."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Date, Select, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Finding,
    PullRequest,
    Repository,
    RepositoryAccess,
    ReviewRun,
)
from app.db.models.enums import FindingFeedback, RunStatus
from app.schemas.dashboard import UsageDay, UsageOut, UsageTotals

MAX_RANGE_DAYS = 366


def _rate(helpful: int, not_helpful: int) -> float | None:
    voted = helpful + not_helpful
    return round(helpful / voted, 4) if voted else None


async def usage(
    db: AsyncSession,
    user_id: uuid.UUID,
    installation_ids: list[uuid.UUID],
    date_from: date,
    date_to: date,
) -> UsageOut:
    """Aggregate review runs created in [date_from, date_to] (UTC days, inclusive) over the
    repositories of `installation_ids` that the user may see (both authorization levels)."""
    start = datetime.combine(date_from, datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    day = cast(func.timezone("UTC", ReviewRun.created_at), Date)

    in_scope = (
        ReviewRun.created_at >= start,
        ReviewRun.created_at < end,
        Repository.installation_id.in_(installation_ids),
        RepositoryAccess.user_id == user_id,
    )

    def scoped(*columns: Any) -> Select[Any]:
        return (
            select(*columns)
            .select_from(ReviewRun)
            .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
            .join(Repository, Repository.id == PullRequest.repository_id)
            .join(RepositoryAccess, RepositoryAccess.repository_id == Repository.id)
            .where(*in_scope)
        )

    # Runs, cost and tokens per day (one row per day that has runs).
    run_rows = (
        await db.execute(
            scoped(
                day.label("day"), func.count(), func.coalesce(func.sum(ReviewRun.cost_usd), 0)
            ).group_by(day)
        )
    ).all()
    per_day = {r[0]: [r[1], float(r[2]), 0, 0] for r in run_rows}

    # Votes per day on findings of those runs.
    vote_rows = (
        await db.execute(
            scoped(day.label("day"), Finding.user_feedback, func.count())
            .join(Finding, Finding.review_run_id == ReviewRun.id)
            .where(Finding.user_feedback.is_not(None))
            .group_by(day, Finding.user_feedback)
        )
    ).all()
    for d, feedback, n in vote_rows:
        entry = per_day.setdefault(d, [0, 0.0, 0, 0])
        entry[2 if feedback == FindingFeedback.HELPFUL.value else 3] += n

    totals_row = (
        await db.execute(
            scoped(
                func.count(),
                func.count().filter(ReviewRun.status == RunStatus.SUCCEEDED.value),
                func.count().filter(ReviewRun.status == RunStatus.FAILED.value),
                func.count().filter(ReviewRun.status == RunStatus.SKIPPED.value),
                func.coalesce(func.sum(ReviewRun.cost_usd), 0),
                func.coalesce(func.sum(ReviewRun.tokens_in), 0),
                func.coalesce(func.sum(ReviewRun.tokens_out), 0),
                func.percentile_cont(0.5).within_group(ReviewRun.latency_ms),
                func.percentile_cont(0.95).within_group(ReviewRun.latency_ms),
            )
        )
    ).one()
    findings_total = (
        await db.scalar(scoped(func.count()).join(Finding, Finding.review_run_id == ReviewRun.id))
        or 0
    )
    helpful = sum(v[2] for v in per_day.values())
    not_helpful = sum(v[3] for v in per_day.values())

    days = []
    current = date_from
    while current <= date_to:
        runs_n, cost, up, down = per_day.get(current, [0, 0.0, 0, 0])
        days.append(
            UsageDay(
                day=current,
                runs=runs_n,
                cost_usd=cost,
                helpful=up,
                not_helpful=down,
                acceptance_rate=_rate(up, down),
            )
        )
        current += timedelta(days=1)

    return UsageOut(
        date_from=date_from,
        date_to=date_to,
        totals=UsageTotals(
            runs=totals_row[0],
            succeeded=totals_row[1],
            failed=totals_row[2],
            skipped=totals_row[3],
            cost_usd=float(totals_row[4]),
            tokens_in=int(totals_row[5]),
            tokens_out=int(totals_row[6]),
            latency_p50_ms=round(totals_row[7]) if totals_row[7] is not None else None,
            latency_p95_ms=round(totals_row[8]) if totals_row[8] is not None else None,
            findings=findings_total,
            helpful=helpful,
            not_helpful=not_helpful,
            acceptance_rate=_rate(helpful, not_helpful),
        ),
        days=days,
    )
