"""Usage analytics (admins)."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.deps import CurrentUserDep, SessionDep
from app.schemas.dashboard import UsageOut
from app.services import analytics, rbac

router = APIRouter(prefix="/analytics", tags=["analytics"])

DEFAULT_RANGE_DAYS = 30


@router.get(
    "/usage",
    response_model=UsageOut,
    summary="Runs, estimated cost, latency and acceptance rate per day (admin)",
    description="Covers the installations where you are admin or owner. Cost is an ESTIMATE at "
    "list price. Acceptance rate = helpful / (helpful + not_helpful) votes.",
    responses={403: {"description": "You are not an admin of any installation"}},
)
async def usage(
    user: CurrentUserDep,
    db: SessionDep,
    date_from: Annotated[
        date | None, Query(alias="from", description="First day (UTC). Default: 29 days ago.")
    ] = None,
    date_to: Annotated[
        date | None, Query(alias="to", description="Last day (UTC), inclusive. Default: today.")
    ] = None,
    installation_id: Annotated[
        uuid.UUID | None, Query(description="Limit to one installation you administer.")
    ] = None,
) -> UsageOut:
    end = date_to or datetime.now(UTC).date()
    start = date_from or end - timedelta(days=DEFAULT_RANGE_DAYS - 1)
    if start > end:
        raise BadRequestError("'from' must not be after 'to'")
    if (end - start).days >= analytics.MAX_RANGE_DAYS:
        raise BadRequestError(f"The range may cover at most {analytics.MAX_RANGE_DAYS} days")

    admin_installations = await rbac.admin_repository_filter(db, user.id, installation_id)
    if not admin_installations:
        # Not an admin there: "not found" if not even a member of the named installation,
        # "forbidden" otherwise.
        if (
            installation_id is not None
            and await rbac.get_membership(db, user.id, installation_id) is None
        ):
            raise NotFoundError("Installation not found")
        raise ForbiddenError("Requires the admin role or higher")
    return await analytics.usage(db, user.id, admin_installations, start, end)
