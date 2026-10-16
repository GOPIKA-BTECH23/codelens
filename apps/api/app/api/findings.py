"""Feedback on a finding."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.db.models import Finding
from app.deps import SessionDep, require_finding_access
from app.schemas.dashboard import FindingOut, FindingUpdate
from app.services import reviews

router = APIRouter(prefix="/findings", tags=["findings"])

FindingDep = Annotated[Finding, Depends(require_finding_access)]


@router.patch(
    "/{finding_id}",
    response_model=FindingOut,
    summary="Rate or dismiss a finding",
    description="`user_feedback` is `helpful` or `not_helpful` (null clears it); `dismissed` "
    "hides the finding from the open count. Any member who can see the repository may do this.",
    responses={404: {"description": "No such finding, or you cannot see its repository"}},
)
async def patch_finding(change: FindingUpdate, finding: FindingDep, db: SessionDep) -> FindingOut:
    return await reviews.update_finding(db, finding, change)
