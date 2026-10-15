"""Users, GitHub App installations, membership and refresh tokens."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UuidPkMixin, check_in
from app.db.models.enums import AccountType, MemberRole


class User(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "users"

    github_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    login: Mapped[str] = mapped_column(String(100))
    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Installation(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "installations"

    github_installation_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    account_login: Mapped[str] = mapped_column(String(100))
    account_type: Mapped[str] = mapped_column(String(20))
    # Set when the GitHub App is uninstalled. The row (and the review metadata hanging off it)
    # is KEPT; a later purge job deletes only code_chunks and chat data. See ADR 0001.
    uninstalled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (check_in("account_type", AccountType),)


class InstallationMember(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "installation_members"

    installation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("installations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))

    __table_args__ = (
        UniqueConstraint("installation_id", "user_id", name="uq_installation_members_pair"),
        check_in("role", MemberRole),
        # The pair constraint's index serves "members of an installation"; this one serves
        # "installations of a user" (every login and every authorization check).
        Index("ix_installation_members_user_id", "user_id"),
    )


class RefreshToken(UuidPkMixin, CreatedAtMixin, Base):
    """One refresh token. Tokens that descend from the same login share a family_id.

    Life cycle: issued at login (new family) -> rotated on use (rotated_at set, a successor
    with the same family and expires_at is issued) -> revoked on logout or reuse detection.
    Presenting a token whose rotated_at is set means someone kept an old copy: the whole
    family is revoked (see app/services/sessions.py).
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    # SHA-256 of the random token. The raw token is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    # Copied unchanged to every successor: a session lasts at most JWT_REFRESH_TTL_DAYS
    # from login, however often it is refreshed (CLAUDE.md decision 14).
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_family_id", "family_id"),
    )


class RateLimitCounter(Base):
    """Fixed-window request counter, one row per key (e.g. "auth:203.0.113.7").

    Lives in Postgres so the limit holds across restarts and processes (decision 22).
    """

    __tablename__ = "rate_limit_counters"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer)

    # Serves the periodic cleanup of keys that have not been seen for a while.
    __table_args__ = (Index("ix_rate_limit_counters_window_start", "window_start"),)
