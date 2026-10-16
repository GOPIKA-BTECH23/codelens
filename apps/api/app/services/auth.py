"""Login sync: turn what GitHub says about a user into our users / installations /
installation_members / repositories / repository_access rows.

Split in two on purpose:
- fetch_login_identity() talks to GitHub and is the ONLY place the user token is used;
- apply_login_identity() writes to the database and never sees the token.
That keeps the token's lifetime as short as possible and makes the DB half testable
without HTTP mocks.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InstallationMember, RepositoryAccess, User
from app.db.models.enums import MemberRole
from app.services.github_oauth import (
    GitHubInstallation,
    GitHubOAuthClient,
    GitHubRepo,
    GitHubUser,
    OrgRole,
)
from app.services.github_sync import ACCOUNT_TYPES, upsert_installation, upsert_repository


@dataclass(frozen=True)
class InstallationGrant:
    """One installation the user can access, their role in it, and the repos they can see."""

    installation: GitHubInstallation
    role: MemberRole
    repos: list[GitHubRepo]


@dataclass(frozen=True)
class LoginIdentity:
    user: GitHubUser
    grants: list[InstallationGrant]


def role_for(
    user: GitHubUser, installation: GitHubInstallation, org_role: OrgRole | None
) -> MemberRole:
    """CLAUDE.md decision 12.

    Personal account: the account's owner is `owner`; anyone else who can see it (a repo
    collaborator) is `member`. Organisation: org owners (GitHub role "admin") are `admin`,
    everyone else is `member`. Nobody becomes `owner` of an organisation installation.
    """
    if installation.account.type == "User":
        return MemberRole.OWNER if installation.account.id == user.id else MemberRole.MEMBER
    return MemberRole.ADMIN if org_role == "admin" else MemberRole.MEMBER


async def fetch_login_identity(gh: GitHubOAuthClient, token: str) -> LoginIdentity:
    """Ask GitHub who the user is and what they can access. Raises GitHubOAuthError."""
    user = await gh.get_user(token)
    grants: list[InstallationGrant] = []
    for installation in await gh.list_installations(token):
        if installation.account.type not in ACCOUNT_TYPES or not installation.account.login:
            continue  # enterprise-level installations are out of scope
        org_role = None
        if installation.account.type == "Organization":
            org_role = await gh.get_org_role(token, installation.account.login)
        grants.append(
            InstallationGrant(
                installation=installation,
                role=role_for(user, installation, org_role),
                repos=await gh.list_installation_repos(token, installation.id),
            )
        )
    return LoginIdentity(user=user, grants=grants)


async def apply_login_identity(session: AsyncSession, identity: LoginIdentity) -> User:
    """Upsert everything and REPLACE the user's memberships and repository access.

    "Replace" matters: an installation or repo that GitHub no longer lists for this user
    loses its row here, so access removed on GitHub is removed at the next login (and
    sessions last at most JWT_REFRESH_TTL_DAYS, decision 14). Only flushes; the request's
    session dependency commits.
    """
    user = await _upsert_user(session, identity.user)

    installation_ids: list[uuid.UUID] = []
    repository_ids: list[uuid.UUID] = []
    for grant in identity.grants:
        installation_id = await upsert_installation(
            session,
            github_installation_id=grant.installation.id,
            account_login=grant.installation.account.login or "",
            account_type=ACCOUNT_TYPES[grant.installation.account.type or ""],
        )
        installation_ids.append(installation_id)
        await _upsert_membership(session, installation_id, user.id, grant.role)
        for repo in grant.repos:
            repository_ids.append(
                await upsert_repository(
                    session,
                    installation_id=installation_id,
                    github_repo_id=repo.id,
                    full_name=repo.full_name,
                    is_private=repo.private,
                    default_branch=repo.default_branch,
                )
            )

    await session.execute(
        delete(InstallationMember).where(
            InstallationMember.user_id == user.id,
            InstallationMember.installation_id.not_in(installation_ids),
        )
    )
    await session.execute(delete(RepositoryAccess).where(RepositoryAccess.user_id == user.id))
    if repository_ids:
        await session.execute(
            insert(RepositoryAccess)
            .values([{"user_id": user.id, "repository_id": rid} for rid in set(repository_ids)])
            .on_conflict_do_nothing()
        )
    await session.flush()
    return user


# --- upserts -------------------------------------------------------------------------------
# INSERT ... ON CONFLICT DO UPDATE keyed on GitHub's numeric ids; installation and
# repository upserts live in github_sync.py because webhooks use them too.


async def _upsert_user(session: AsyncSession, gh_user: GitHubUser) -> User:
    values = {
        "login": gh_user.login,
        "name": gh_user.name,
        "email": gh_user.email,
        "avatar_url": gh_user.avatar_url,
        "last_login_at": func.now(),
    }
    stmt = (
        insert(User)
        .values(github_id=gh_user.id, **values)
        .on_conflict_do_update(
            index_elements=[User.github_id], set_={**values, "updated_at": func.now()}
        )
        .returning(User)
    )
    # populate_existing: if this User is already in the session's identity map, refresh it.
    return (await session.scalars(stmt, execution_options={"populate_existing": True})).one()


async def _upsert_membership(
    session: AsyncSession, installation_id: uuid.UUID, user_id: uuid.UUID, role: MemberRole
) -> None:
    stmt = (
        insert(InstallationMember)
        .values(installation_id=installation_id, user_id=user_id, role=role.value)
        .on_conflict_do_update(
            constraint="uq_installation_members_pair",
            set_={"role": role.value, "updated_at": func.now()},
        )
    )
    await session.execute(stmt)
