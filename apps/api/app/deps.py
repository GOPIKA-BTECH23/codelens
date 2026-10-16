"""Shared FastAPI dependencies: database session, settings, current user and RBAC guards.

Authorization lives here (not in route bodies) so a route cannot forget it: declaring
`Depends(require_installation_role(MemberRole.ADMIN))` is the whole check, and it runs
before the handler.
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

import httpx
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import ForbiddenError, NotFoundError, RateLimitedError, UnauthorizedError
from app.core.rate_limit import RateLimiter
from app.core.security import ACCESS_COOKIE, decode_access_token
from app.db.models import Finding, Repository, User
from app.db.models.enums import MemberRole
from app.db.session import get_session
from app.services import rbac
from app.services.github_oauth import GitHubOAuthClient
from app.services.jobs import JobQueue

# scope="function": commit/rollback runs when the route function returns, before the
# response is sent. See get_session.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_job_queue(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.job_queue
    return queue


JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]


# --- authentication ------------------------------------------------------------------------


async def get_current_user(request: Request, db: SessionDep, settings: SettingsDep) -> User:
    """The signed-in user, from the access-token cookie. 401 if absent, invalid or expired.

    The token says only who you are. Whether the user still exists is checked against the
    database, and roles are looked up by the guards below on every request.
    """
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise UnauthorizedError()
    user_id = decode_access_token(token, settings)
    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise UnauthorizedError()
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


# --- authorization -------------------------------------------------------------------------


def require_installation_role(
    min_role: MemberRole,
) -> Callable[..., Awaitable[rbac.Membership]]:
    """Dependency factory: the route's `installation_id` path parameter must be an
    installation where the current user holds at least `min_role`.

    Not a member at all -> 404 (do not confirm that the installation exists).
    Member, but the role is too weak -> 403 (they already know it exists).
    """

    async def guard(
        installation_id: uuid.UUID, user: CurrentUserDep, db: SessionDep
    ) -> rbac.Membership:
        membership = await rbac.get_membership(db, user.id, installation_id)
        if membership is None:
            raise NotFoundError("Installation not found")
        if not rbac.role_at_least(membership.role, min_role):
            raise ForbiddenError(f"Requires the {min_role.value} role or higher")
        return membership

    return guard


async def _repository_access(
    db: AsyncSession, user: User, repository_id: uuid.UUID
) -> rbac.RepositoryAccessInfo:
    access = await rbac.get_repository_access(db, user.id, repository_id)
    if access is None:
        raise NotFoundError("Repository not found")  # same answer for "missing" and "not yours"
    return access


async def require_repository_access(
    repository_id: uuid.UUID, user: CurrentUserDep, db: SessionDep
) -> Repository:
    """The route's `repository_id` must be a repo the user may see (both levels). 404
    otherwise. Every repo-scoped endpoint declares this (or one of the guards below)."""
    return (await _repository_access(db, user, repository_id)).repository


def require_repository_role(
    min_role: MemberRole,
) -> Callable[..., Awaitable[rbac.RepositoryAccessInfo]]:
    """Like require_installation_role, for `repository_id` routes: visible repo (404
    otherwise) AND at least `min_role` in its installation (403 otherwise)."""

    async def guard(
        repository_id: uuid.UUID, user: CurrentUserDep, db: SessionDep
    ) -> rbac.RepositoryAccessInfo:
        access = await _repository_access(db, user, repository_id)
        if not rbac.role_at_least(access.role, min_role):
            raise ForbiddenError(f"Requires the {min_role.value} role or higher")
        return access

    return guard


async def require_pull_access(
    pull_id: uuid.UUID, user: CurrentUserDep, db: SessionDep
) -> rbac.PullAccess:
    """The route's `pull_id` must be a PR in a repo the user may see. 404 otherwise."""
    found = await rbac.get_pull_access(db, user.id, pull_id)
    if found is None:
        raise NotFoundError("Pull request not found")
    return found


async def require_finding_access(
    finding_id: uuid.UUID, user: CurrentUserDep, db: SessionDep
) -> Finding:
    """The route's `finding_id` must belong to a PR in a repo the user may see. 404 otherwise."""
    found = await rbac.get_finding_access(db, user.id, finding_id)
    if found is None:
        raise NotFoundError("Finding not found")
    return found[0]


# --- outbound GitHub and rate limiting -----------------------------------------------------


async def get_github_oauth_client(
    settings: SettingsDep,
) -> AsyncIterator[GitHubOAuthClient]:
    """A short-lived HTTP client per login. Logins are rare, so pooling buys nothing."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as http:
        yield GitHubOAuthClient(http, settings)


def client_ip(request: Request) -> str:
    """The peer address as uvicorn reports it.

    Behind Vercel and Render the peer is a proxy; uvicorn must be told which proxies to
    trust (--forwarded-allow-ips) so that this becomes the real client IP. That is an M7
    deployment task; X-Forwarded-For is never read here directly because clients can forge it.
    """
    return request.client.host if request.client else "unknown"


async def auth_rate_limit(request: Request, settings: SettingsDep) -> None:
    """Per-IP limit on /auth/*: slows down brute force and refresh/login floods."""
    limiter: RateLimiter = request.app.state.rate_limiter
    result = await limiter.hit(
        f"auth:{client_ip(request)}", limit=settings.auth_rate_limit_per_minute, window_seconds=60
    )
    if not result.allowed:
        raise RateLimitedError(result.retry_after_seconds)
