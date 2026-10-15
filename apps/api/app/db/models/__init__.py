"""Import every model so Base.metadata knows all tables (Alembic autogenerate needs this)."""

from app.db.models.accounts import (
    Installation,
    InstallationMember,
    RateLimitCounter,
    RefreshToken,
    User,
)
from app.db.models.code import ChatMessage, ChatSession, CodeChunk, Job, WebhookDelivery
from app.db.models.repos import Finding, PullRequest, Repository, RepositoryAccess, ReviewRun

__all__ = [
    "ChatMessage",
    "ChatSession",
    "CodeChunk",
    "Finding",
    "Installation",
    "InstallationMember",
    "Job",
    "PullRequest",
    "RateLimitCounter",
    "RefreshToken",
    "Repository",
    "RepositoryAccess",
    "ReviewRun",
    "User",
    "WebhookDelivery",
]
