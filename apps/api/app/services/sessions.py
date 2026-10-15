"""Our own sessions: issue, rotate and revoke refresh tokens (refresh_tokens table).

Rotation with reuse detection, in plain words:
- every login starts a FAMILY; every refresh marks the presented token `rotated_at` and
  issues a successor in the same family;
- a legitimate client only ever holds the newest token, so seeing an already-rotated token
  means two parties hold copies (e.g. a stolen cookie). We cannot tell which one is the
  attacker, so the whole family is revoked and both must log in again.
"""

import enum
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.security import create_access_token, hash_token, new_refresh_token
from app.db.models import RefreshToken

logger = logging.getLogger("app.auth")


@dataclass(frozen=True)
class IssuedSession:
    """Raw tokens to put in cookies. They exist only in this object and the response."""

    user_id: uuid.UUID
    access_token: str
    refresh_token: str
    refresh_expires_at: datetime


class RefreshFailure(enum.StrEnum):
    UNKNOWN = "unknown"  # no such token (garbage, or from another database)
    EXPIRED = "expired"  # past the session's absolute lifetime
    REVOKED = "revoked"  # logged out, or its family was revoked earlier
    REUSED = "reused"  # already rotated: family revoked just now


@dataclass(frozen=True)
class RefreshResult:
    session: IssuedSession | None = None
    failure: RefreshFailure | None = None


def _now() -> datetime:
    return datetime.now(UTC)


async def start_session(db: AsyncSession, user_id: uuid.UUID, settings: Settings) -> IssuedSession:
    """New login: a new token family with an absolute expiry of JWT_REFRESH_TTL_DAYS."""
    expires_at = _now() + timedelta(days=settings.jwt_refresh_ttl_days)
    return await _issue(db, user_id, uuid.uuid4(), expires_at, settings)


async def rotate_refresh_token(
    db: AsyncSession, raw_token: str, settings: Settings
) -> RefreshResult:
    """Exchange a refresh token for a new access + refresh pair, or explain why not.

    Returns instead of raising so the caller can COMMIT a family revocation while still
    answering 401 (an exception would roll it back).
    """
    # FOR UPDATE: two concurrent refreshes with the same token are serialised. The second
    # one sees rotated_at set and is treated as reuse, never as a second valid rotation.
    token = await db.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_token(raw_token))
        .with_for_update()
    )
    if token is None:
        return RefreshResult(failure=RefreshFailure.UNKNOWN)
    if token.revoked_at is not None:
        return RefreshResult(failure=RefreshFailure.REVOKED)
    if token.rotated_at is not None:
        await revoke_family(db, token.family_id)
        logger.warning(
            "refresh token reuse detected; family revoked",
            extra={"user_id": str(token.user_id), "family_id": str(token.family_id)},
        )
        return RefreshResult(failure=RefreshFailure.REUSED)
    if token.expires_at <= _now():
        return RefreshResult(failure=RefreshFailure.EXPIRED)

    token.rotated_at = _now()
    # Same family and the SAME expires_at: refreshing never extends the session (decision 14).
    issued = await _issue(db, token.user_id, token.family_id, token.expires_at, settings)
    return RefreshResult(session=issued)


async def revoke_session(db: AsyncSession, raw_token: str) -> None:
    """Logout: revoke the family of the presented token (a no-op for unknown tokens)."""
    family_id = await db.scalar(
        select(RefreshToken.family_id).where(RefreshToken.token_hash == hash_token(raw_token))
    )
    if family_id is not None:
        await revoke_family(db, family_id)


async def revoke_family(db: AsyncSession, family_id: uuid.UUID) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


async def _issue(
    db: AsyncSession,
    user_id: uuid.UUID,
    family_id: uuid.UUID,
    expires_at: datetime,
    settings: Settings,
) -> IssuedSession:
    raw = new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user_id, token_hash=hash_token(raw), family_id=family_id, expires_at=expires_at
        )
    )
    await db.flush()
    return IssuedSession(
        user_id=user_id,
        access_token=create_access_token(user_id, settings),
        refresh_token=raw,
        refresh_expires_at=expires_at,
    )
