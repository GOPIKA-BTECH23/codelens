"""Repositories: list, detail, settings, "index now". Pull requests of a repo are listed here
too (GET /repos/{id}/pulls) because they are scoped by the repository guard."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status

from app.core.pagination import Page, PageParamsDep
from app.db.models.enums import MemberRole, PullRequestState
from app.deps import CurrentUserDep, JobQueueDep, SessionDep, require_repository_role
from app.schemas.dashboard import (
    IndexRequested,
    PullOut,
    RepoDetail,
    RepoOut,
    RepoSettings,
    RepoSettingsUpdate,
)
from app.services import rbac, repos, reviews

router = APIRouter(prefix="/repos", tags=["repos"])

MemberDep = Annotated[
    rbac.RepositoryAccessInfo, Depends(require_repository_role(MemberRole.MEMBER))
]
AdminDep = Annotated[rbac.RepositoryAccessInfo, Depends(require_repository_role(MemberRole.ADMIN))]

NOT_FOUND = {404: {"description": "No such repository, or you cannot see it"}}
FORBIDDEN = {403: {"description": "Admin or owner role required"}}


@router.get(
    "",
    response_model=Page[RepoOut],
    summary="Repositories you can see",
    description="Repositories of every installation you belong to, limited to the ones GitHub "
    "says you can access. Each item carries your role and the latest review run.",
)
async def list_repositories(
    user: CurrentUserDep,
    db: SessionDep,
    page: PageParamsDep,
    installation_id: Annotated[
        uuid.UUID | None, Query(description="Only this installation.")
    ] = None,
    q: Annotated[str | None, Query(max_length=100, description="Name contains.")] = None,
) -> Page[RepoOut]:
    return await repos.list_repos(db, user.id, page, installation_id=installation_id, query=q)


@router.get(
    "/{repository_id}",
    response_model=RepoDetail,
    summary="One repository with its effective review settings",
    responses=NOT_FOUND,
)
async def get_repository(access: MemberDep, db: SessionDep) -> RepoDetail:
    return await repos.repo_detail(db, access.repository, access.role)


@router.post(
    "/{repository_id}/index",
    response_model=IndexRequested,
    status_code=http_status.HTTP_202_ACCEPTED,
    summary="Queue indexing of the repository (admin)",
    description="Enqueues an `index_repo` job. The indexer itself arrives in a later "
    "milestone: until then the worker marks the repository `failed` with a clear reason.",
    responses={**NOT_FOUND, **FORBIDDEN},
)
async def index_repository(access: AdminDep, db: SessionDep, queue: JobQueueDep) -> IndexRequested:
    enqueued = await repos.request_indexing(db, queue, access.repository)
    return IndexRequested(
        index_status=access.repository.index_status,  # type: ignore[arg-type]
        enqueued=enqueued,
    )


@router.patch(
    "/{repository_id}/settings",
    response_model=RepoSettings,
    summary="Change review settings (admin)",
    description="Partial update: send only the fields to change.",
    responses={**NOT_FOUND, **FORBIDDEN},
)
async def update_repository_settings(
    change: RepoSettingsUpdate, access: AdminDep, db: SessionDep
) -> RepoSettings:
    return await repos.update_settings(db, access.repository, change)


@router.get(
    "/{repository_id}/pulls",
    response_model=Page[PullOut],
    summary="Pull requests of a repository",
    description="Most recently updated first. Each item has the latest review run's status "
    "and its number of open findings.",
    responses=NOT_FOUND,
)
async def list_pulls(
    access: MemberDep,
    db: SessionDep,
    page: PageParamsDep,
    state: Annotated[PullRequestState | None, Query(description="Filter by PR state.")] = None,
) -> Page[PullOut]:
    return await reviews.list_pulls(db, access.repository.id, page, state=state)
