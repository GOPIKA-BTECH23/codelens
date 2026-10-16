"""Authorization queries. Authentication says WHO you are; this module says what you may see.

Two levels (CLAUDE.md section 7):
1. installation membership gives a role: owner > admin > member;
2. repository_access says which repos of that installation the user may see.
Uninstalled installations are invisible to everyone (their rows are kept for history).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Finding,
    Installation,
    InstallationMember,
    PullRequest,
    Repository,
    RepositoryAccess,
    ReviewRun,
    User,
)
from app.db.models.enums import MemberRole

_ROLE_RANK = {MemberRole.MEMBER: 1, MemberRole.ADMIN: 2, MemberRole.OWNER: 3}


def role_at_least(role: MemberRole | str, minimum: MemberRole) -> bool:
    """True when `role` is `minimum` or stronger."""
    return _ROLE_RANK[MemberRole(role)] >= _ROLE_RANK[minimum]


@dataclass(frozen=True)
class Membership:
    installation: Installation
    role: MemberRole


async def get_membership(
    db: AsyncSession, user_id: uuid.UUID, installation_id: uuid.UUID
) -> Membership | None:
    """The user's role in one (still installed) installation, or None."""
    row = (
        await db.execute(
            select(Installation, InstallationMember.role)
            .join(InstallationMember, InstallationMember.installation_id == Installation.id)
            .where(
                Installation.id == installation_id,
                Installation.uninstalled_at.is_(None),
                InstallationMember.user_id == user_id,
            )
        )
    ).one_or_none()
    return Membership(installation=row[0], role=MemberRole(row[1])) if row else None


async def list_memberships(db: AsyncSession, user_id: uuid.UUID) -> list[Membership]:
    rows = await db.execute(
        select(Installation, InstallationMember.role)
        .join(InstallationMember, InstallationMember.installation_id == Installation.id)
        .where(InstallationMember.user_id == user_id, Installation.uninstalled_at.is_(None))
        .order_by(Installation.account_login)
    )
    return [Membership(installation=inst, role=MemberRole(role)) for inst, role in rows.all()]


async def list_members(
    db: AsyncSession, installation_id: uuid.UUID
) -> list[tuple[User, MemberRole]]:
    rows = await db.execute(
        select(User, InstallationMember.role)
        .join(InstallationMember, InstallationMember.user_id == User.id)
        .where(InstallationMember.installation_id == installation_id)
        .order_by(User.login)
    )
    return [(user, MemberRole(role)) for user, role in rows.all()]


@dataclass(frozen=True)
class RepositoryAccessInfo:
    """A repository the user may see, with their role in its installation."""

    repository: Repository
    role: MemberRole


async def get_repository_access(
    db: AsyncSession, user_id: uuid.UUID, repository_id: uuid.UUID
) -> RepositoryAccessInfo | None:
    """The repository and the user's role, if BOTH levels allow it: the user is a member of
    its (installed) installation AND has a repository_access row for it. Otherwise None."""
    row = (
        await db.execute(
            select(Repository, InstallationMember.role)
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
            .where(Repository.id == repository_id, Installation.uninstalled_at.is_(None))
        )
    ).one_or_none()
    return RepositoryAccessInfo(repository=row[0], role=MemberRole(row[1])) if row else None


async def get_accessible_repository(
    db: AsyncSession, user_id: uuid.UUID, repository_id: uuid.UUID
) -> Repository | None:
    """Like get_repository_access, without the role."""
    info = await get_repository_access(db, user_id, repository_id)
    return info.repository if info else None


@dataclass(frozen=True)
class PullAccess:
    pull: PullRequest
    access: RepositoryAccessInfo


async def get_pull_access(
    db: AsyncSession, user_id: uuid.UUID, pull_id: uuid.UUID
) -> PullAccess | None:
    """A pull request, if the user may see its repository (None otherwise: same answer for
    "does not exist" and "not yours")."""
    pull = await db.scalar(select(PullRequest).where(PullRequest.id == pull_id))
    if pull is None:
        return None
    access = await get_repository_access(db, user_id, pull.repository_id)
    return PullAccess(pull=pull, access=access) if access else None


async def get_finding_access(
    db: AsyncSession, user_id: uuid.UUID, finding_id: uuid.UUID
) -> tuple[Finding, RepositoryAccessInfo] | None:
    """A finding, if the user may see the repository of the PR it was posted on."""
    row = (
        await db.execute(
            select(Finding, PullRequest.repository_id)
            .join(ReviewRun, ReviewRun.id == Finding.review_run_id)
            .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
            .where(Finding.id == finding_id)
        )
    ).one_or_none()
    if row is None:
        return None
    access = await get_repository_access(db, user_id, row[1])
    return (row[0], access) if access else None


async def admin_repository_filter(
    db: AsyncSession, user_id: uuid.UUID, installation_id: uuid.UUID | None = None
) -> list[uuid.UUID]:
    """Installations where the user is admin or owner (optionally just one)."""
    stmt = (
        select(Installation.id)
        .join(InstallationMember, InstallationMember.installation_id == Installation.id)
        .where(
            InstallationMember.user_id == user_id,
            InstallationMember.role.in_([MemberRole.ADMIN.value, MemberRole.OWNER.value]),
            Installation.uninstalled_at.is_(None),
        )
    )
    if installation_id is not None:
        stmt = stmt.where(Installation.id == installation_id)
    return list(await db.scalars(stmt))
