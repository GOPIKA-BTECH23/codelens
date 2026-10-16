"""Repository queries for the dashboard: list, detail, settings, "index now"."""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams
from app.db.models import (
    Installation,
    InstallationMember,
    PullRequest,
    Repository,
    RepositoryAccess,
    ReviewRun,
)
from app.db.models.enums import IndexStatus, MemberRole
from app.schemas.dashboard import RepoDetail, RepoOut, RepoSettings, RepoSettingsUpdate, RunBrief
from app.services.jobs import JobQueue
from app.services.webhooks import JOB_INDEX_REPO


def effective_settings(stored: dict[str, Any]) -> RepoSettings:
    """Stored JSON merged over the defaults. Unknown keys (from a future version) are dropped
    instead of breaking the page."""
    known = {k: v for k, v in stored.items() if k in RepoSettings.model_fields}
    return RepoSettings.model_validate(known)


async def latest_runs(
    db: AsyncSession, repository_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, RunBrief]:
    """The most recent run per repository (one query, DISTINCT ON)."""
    if not repository_ids:
        return {}
    rows = await db.execute(
        select(
            PullRequest.repository_id,
            ReviewRun.id,
            ReviewRun.status,
            ReviewRun.created_at,
            PullRequest.id.label("pull_id"),
            PullRequest.number,
            PullRequest.title,
        )
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .where(PullRequest.repository_id.in_(repository_ids))
        .ext(distinct_on(PullRequest.repository_id))
        .order_by(PullRequest.repository_id, ReviewRun.created_at.desc())
    )
    return {
        r.repository_id: RunBrief(
            id=r.id,
            status=r.status,
            created_at=r.created_at,
            pull_request_id=r.pull_id,
            pull_number=r.number,
            pull_title=r.title,
        )
        for r in rows
    }


def _repo_fields(
    repo: Repository, login: str, role: str, runs: dict[uuid.UUID, RunBrief]
) -> dict[str, Any]:
    return {
        "id": repo.id,
        "installation_id": repo.installation_id,
        "installation_login": login,
        "full_name": repo.full_name,
        "default_branch": repo.default_branch,
        "is_private": repo.is_private,
        "index_status": repo.index_status,
        "last_indexed_sha": repo.last_indexed_sha,
        "role": MemberRole(role),
        "latest_run": runs.get(repo.id),
    }


async def list_repos(
    db: AsyncSession,
    user_id: uuid.UUID,
    page: PageParams,
    *,
    installation_id: uuid.UUID | None = None,
    query: str | None = None,
) -> Page[RepoOut]:
    """Repositories the user may see: member of the installation AND a repository_access row."""
    visible = (
        select(Repository, Installation.account_login, InstallationMember.role)
        .join(Installation, Installation.id == Repository.installation_id)
        .join(
            InstallationMember,
            (InstallationMember.installation_id == Installation.id)
            & (InstallationMember.user_id == user_id),
        )
        .join(
            RepositoryAccess,
            (RepositoryAccess.repository_id == Repository.id)
            & (RepositoryAccess.user_id == user_id),
        )
        .where(Installation.uninstalled_at.is_(None))
    )
    if installation_id is not None:
        visible = visible.where(Repository.installation_id == installation_id)
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        visible = visible.where(Repository.full_name.ilike(f"%{escaped}%", escape="\\"))

    total = await db.scalar(select(func.count()).select_from(visible.subquery())) or 0
    rows = (
        await db.execute(
            visible.order_by(Repository.full_name).limit(page.limit).offset(page.offset)
        )
    ).all()
    runs = await latest_runs(db, [r[0].id for r in rows])
    items = [RepoOut(**_repo_fields(repo, login, role, runs)) for repo, login, role in rows]
    return Page[RepoOut](items=items, total=total, limit=page.limit, offset=page.offset)


async def repo_detail(db: AsyncSession, repo: Repository, role: MemberRole) -> RepoDetail:
    login = await db.scalar(
        select(Installation.account_login).where(Installation.id == repo.installation_id)
    )
    runs = await latest_runs(db, [repo.id])
    return RepoDetail(
        **_repo_fields(repo, login or "", role.value, runs),
        settings=effective_settings(repo.settings),
    )


async def update_settings(
    db: AsyncSession, repo: Repository, change: RepoSettingsUpdate
) -> RepoSettings:
    """Apply a partial update on top of the current effective settings and save the result."""
    current = effective_settings(repo.settings).model_dump(mode="json")
    current.update(change.model_dump(mode="json", exclude_unset=True, exclude_none=True))
    merged = RepoSettings.model_validate(current)  # validates the merged result
    repo.settings = merged.model_dump(mode="json")
    await db.flush()
    return merged


async def request_indexing(db: AsyncSession, queue: JobQueue, repo: Repository) -> bool:
    """Queue an index_repo job unless one is already queued or running. Returns whether a
    job was enqueued. The handler arrives in M8 (until then the worker marks the repo failed)."""
    if repo.index_status in (IndexStatus.QUEUED, IndexStatus.INDEXING):
        return False
    await db.execute(
        update(Repository).where(Repository.id == repo.id).values(index_status=IndexStatus.QUEUED)
    )
    repo.index_status = IndexStatus.QUEUED.value
    await queue.enqueue(db, JOB_INDEX_REPO, {"repository_id": str(repo.id), "commit_sha": None})
    return True
