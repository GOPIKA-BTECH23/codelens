"""String enums for constrained columns. Stored as VARCHAR + CHECK (see base.check_in)."""

from enum import StrEnum


class AccountType(StrEnum):
    USER = "user"
    ORGANIZATION = "organization"


class MemberRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class IndexStatus(StrEnum):
    NOT_INDEXED = "not_indexed"
    QUEUED = "queued"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class PullRequestState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingFeedback(StrEnum):
    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class JobStatus(StrEnum):
    QUEUED = "queued"  # waiting (also: failed once, waiting for its backoff to pass)
    RUNNING = "running"  # claimed by a worker
    SUCCEEDED = "succeeded"
    DEAD = "dead"  # out of attempts; needs a human
