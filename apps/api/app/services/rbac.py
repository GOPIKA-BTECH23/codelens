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

from app.db.models import Installation, InstallationMember, Repository, RepositoryAccess, User
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


async def get_accessible_repository(
    db: AsyncSession, user_id: uuid.UUID, repository_id: uuid.UUID
) -> Repository | None:
    """The repository, if BOTH levels allow it: the user is a member of its (installed)
    installation AND has a repository_access row for it. Otherwise None."""
    return await db.scalar(
        select(Repository)
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
