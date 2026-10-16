"""Daily LLM cost cap (DAILY_COST_CAP_USD).

M4 ships only the hook: `review_runs.cost_usd` stays NULL until M5 records real (estimated, at
list price) costs, so today's total is 0 and nothing is skipped until then. "Today" is the UTC
calendar day on the DATABASE clock, so all workers agree on when the budget resets.
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.repos import ReviewRun


async def today_cost_usd(session: AsyncSession) -> Decimal:
    """Sum of cost_usd over runs that started since 00:00 UTC today."""
    utc_now = func.timezone("UTC", func.now())  # timestamp without tz, in UTC
    start_of_utc_day = func.timezone("UTC", func.date_trunc("day", utc_now))  # back to timestamptz
    total = await session.scalar(
        select(func.coalesce(func.sum(ReviewRun.cost_usd), 0)).where(
            ReviewRun.started_at >= start_of_utc_day
        )
    )
    return Decimal(total or 0)


async def cap_reached(session: AsyncSession, cap_usd: Decimal) -> Decimal | None:
    """Today's spend if it has reached the cap, else None. A cap of 0 blocks every run."""
    spent = await today_cost_usd(session)
    return spent if spent >= cap_usd else None
