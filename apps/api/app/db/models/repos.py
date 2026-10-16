"""Repositories, pull requests, review runs and findings."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UuidPkMixin, check_in
from app.db.models.enums import (
    FindingFeedback,
    IndexStatus,
    PullRequestState,
    RunStatus,
    Severity,
)

# Every FK below uses ON DELETE CASCADE, but cascades only matter for HARD deletes (an admin
# deleting a row, a test cleaning up). Uninstalling the app is NOT a delete: it sets
# installations.uninstalled_at and a purge job removes only code_chunks and chat data, so
# review metadata survives. See docs/adr/0001-database-schema.md.


class Repository(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "repositories"

    installation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("installations.id", ondelete="CASCADE")
    )
    github_repo_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    full_name: Mapped[str] = mapped_column(String(255))
    default_branch: Mapped[str] = mapped_column(String(255), server_default="main")
    is_private: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    index_status: Mapped[str] = mapped_column(
        String(20), server_default=IndexStatus.NOT_INDEXED.value
    )
    last_indexed_sha: Mapped[str | None] = mapped_column(String(40))
    # M6: per-repo review settings (validated by app.services.repos.RepoSettings). Empty = defaults.
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )

    __table_args__ = (
        check_in("index_status", IndexStatus),
        Index("ix_repositories_installation_id", "installation_id"),
    )


class RepositoryAccess(UuidPkMixin, CreatedAtMixin, Base):
    """Which repositories of an installation a user may see (CLAUDE.md decision 13).

    Installation membership gives the role; this table narrows visibility to the repos
    GitHub says the user can access. Replaced wholesale for the user at every login.
    """

    __tablename__ = "repository_access"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("repositories.id", ondelete="CASCADE")
    )

    __table_args__ = (
        # Also serves "repos of a user" (the leading column).
        UniqueConstraint("user_id", "repository_id", name="uq_repository_access_pair"),
        Index("ix_repository_access_repository_id", "repository_id"),
    )


class PullRequest(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "pull_requests"

    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("repositories.id", ondelete="CASCADE")
    )
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    author_login: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(20), server_default=PullRequestState.OPEN.value)

    __table_args__ = (
        UniqueConstraint("repository_id", "number", name="uq_pull_requests_repo_number"),
        check_in("state", PullRequestState),
    )


class ReviewRun(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "review_runs"

    pull_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pull_requests.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(20), server_default=RunStatus.QUEUED.value)
    model: Mapped[str | None] = mapped_column(String(100))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    # Estimated cost at list price (CLAUDE.md decision 8), never a real bill.
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    # M5: the PR head commit the review was computed for (the review's commit_id), and the
    # id of the review we posted on GitHub (NULL until posted).
    head_sha: Mapped[str | None] = mapped_column(String(40))
    github_review_id: Mapped[int | None] = mapped_column(BigInteger)
    # M6: what the dashboard shows above the diff: {"what_changed": str | None, "notes": [str]}.
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        check_in("status", RunStatus),
        # "latest runs of a PR" and "supersede the older queued/running runs of a PR".
        Index("ix_review_runs_pull_request_id_created_at", "pull_request_id", "created_at"),
        Index("ix_review_runs_status", "status"),
    )


class Finding(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "findings"

    review_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("review_runs.id", ondelete="CASCADE")
    )
    file: Mapped[str] = mapped_column(String(1000))
    line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)  # M5: last line of a multi-line finding
    severity: Mapped[str] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(50))
    # message and suggestion can quote code, so they are nullable: the retention job sets
    # them to NULL 30 days after uninstall (CLAUDE.md section 7).
    message: Mapped[str | None] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    user_feedback: Mapped[str | None] = mapped_column(String(20))
    dismissed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)

    __table_args__ = (
        check_in("severity", Severity),
        check_in("user_feedback", FindingFeedback),
        Index("ix_findings_review_run_id", "review_run_id"),
    )


class ReviewRunFile(UuidPkMixin, CreatedAtMixin, Base):
    """One reviewed file of a run, with its diff patch, so the dashboard can render the diff
    without calling GitHub. The patch is source code: it is purged with code_chunks on
    uninstall and 30 days after the PR is closed or merged (docs/adr/0006)."""

    __tablename__ = "review_run_files"

    review_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("review_runs.id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(String(1000))
    old_path: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(20))
    additions: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    deletions: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # NULL once purged (or when GitHub sent no patch).
    patch: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("review_run_id", "path", name="uq_review_run_files_run_path"),
        Index("ix_review_run_files_review_run_id", "review_run_id"),
    )
