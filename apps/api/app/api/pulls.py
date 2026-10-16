"""One pull request and its latest review."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.deps import SessionDep, require_pull_access
from app.schemas.dashboard import PullOut, ReviewOut
from app.services import rbac, reviews

router = APIRouter(prefix="/pulls", tags=["pulls"])

PullDep = Annotated[rbac.PullAccess, Depends(require_pull_access)]
NOT_FOUND = {404: {"description": "No such pull request, or you cannot see its repository"}}


@router.get("/{pull_id}", response_model=PullOut, summary="One pull request", responses=NOT_FOUND)
async def get_pull(found: PullDep, db: SessionDep) -> PullOut:
    return await reviews.pull_out(db, found.pull)


@router.get(
    "/{pull_id}/review",
    response_model=ReviewOut | None,
    summary="Latest review run with findings and the reviewed diff",
    description="`null` when the pull request has not been reviewed yet. `files[].hunks` is "
    "the stored diff of each reviewed file; `patch_available` is false for runs that "
    "predate diff storage or whose diff was purged.",
    responses=NOT_FOUND,
)
async def get_review(found: PullDep, db: SessionDep) -> ReviewOut | None:
    return await reviews.latest_review(db, found.pull.id)
