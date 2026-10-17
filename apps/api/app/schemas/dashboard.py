"""Response and request models for the dashboard endpoints (M6).

The web app's TypeScript types are generated from these (apps/web/src/api/schema.ts via
`npm run gen:api-types`), so a field added here shows up in the frontend after one command.
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.enums import (
    FindingFeedback,
    IndexStatus,
    MemberRole,
    PullRequestState,
    RunStatus,
    Severity,
)

AgentName = Literal["security"]  # the style and test-gap agents arrive in a later milestone


class RepoSettings(BaseModel):
    """Per-repository review settings (stored in repositories.settings)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "enabled_agents": ["security"],
                    "severity_threshold": "medium",
                    "ignore_paths": ["docs/**", "**/*.generated.ts"],
                }
            ]
        },
    )

    enabled_agents: list[AgentName] = Field(
        default=["security"], description="Agents that run on pull requests of this repository."
    )
    severity_threshold: Severity = Field(
        default=Severity.INFO,
        description="Findings below this severity are not posted (info = post everything).",
    )
    ignore_paths: list[str] = Field(
        default=[],
        max_length=50,
        description="Glob patterns of files that are never reviewed, e.g. `vendor/**`.",
    )


class RepoSettingsUpdate(BaseModel):
    """Partial update: only the fields you send change."""

    model_config = ConfigDict(extra="forbid")

    enabled_agents: list[AgentName] | None = None
    severity_threshold: Severity | None = None
    ignore_paths: list[str] | None = Field(default=None, max_length=50)


class RunBrief(BaseModel):
    id: uuid.UUID
    status: RunStatus
    created_at: datetime
    pull_request_id: uuid.UUID
    pull_number: int
    pull_title: str
    review_posted: bool = Field(
        description="True when a review was posted to GitHub (github_review_id is set)."
    )


class RepoOut(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "5b6f0d57-6b1f-4f55-9a52-5d1f6a1b0c11",
                    "installation_id": "0d1e7a3c-2a7e-4c0e-8f55-0f1a53c3a001",
                    "installation_login": "acme",
                    "full_name": "acme/api",
                    "default_branch": "main",
                    "is_private": False,
                    "index_status": "not_indexed",
                    "last_indexed_sha": None,
                    "role": "admin",
                    "latest_run": None,
                }
            ]
        }
    )

    id: uuid.UUID
    installation_id: uuid.UUID
    installation_login: str
    full_name: str
    default_branch: str
    is_private: bool
    index_status: IndexStatus
    last_indexed_sha: str | None
    role: MemberRole = Field(description="Your role in the repository's installation.")
    latest_run: RunBrief | None = Field(description="The most recent review run, if any.")


class RepoDetail(RepoOut):
    settings: RepoSettings = Field(description="Effective settings (defaults filled in).")


class IndexRequested(BaseModel):
    index_status: IndexStatus
    enqueued: bool = Field(description="False when indexing was already queued or running.")


class RunSummary(BaseModel):
    id: uuid.UUID
    status: RunStatus
    created_at: datetime
    findings_count: int = Field(description="Findings not dismissed.")
    review_posted: bool = Field(
        description="True when a review was posted to GitHub (github_review_id is set)."
    )


class PullOut(BaseModel):
    id: uuid.UUID
    repository_id: uuid.UUID
    number: int
    title: str
    author_login: str
    state: PullRequestState
    created_at: datetime
    updated_at: datetime
    latest_run: RunSummary | None


class RunOut(BaseModel):
    id: uuid.UUID
    status: RunStatus
    model: str | None
    prompt_version: str | None
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None = Field(description="ESTIMATED cost at list price, not a bill.")
    latency_ms: int | None
    head_sha: str | None
    error: str | None = Field(description="Why the run failed or was skipped.")
    created_at: datetime
    finished_at: datetime | None
    review_posted: bool = Field(
        description="True when a review was posted to GitHub (github_review_id is set)."
    )


class SummaryOut(BaseModel):
    what_changed: str | None
    notes: list[str]


class FindingOut(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "9c2f6b0e-0a41-4b8e-9d6f-3a1f5d0c7e22",
                    "file": "app/db.py",
                    "line": 12,
                    "end_line": None,
                    "severity": "high",
                    "category": "sql-injection",
                    "message": "User input is concatenated into the query.",
                    "suggestion": "Use a parameterised query.",
                    "confidence": 0.9,
                    "user_feedback": None,
                    "dismissed": False,
                }
            ]
        }
    )

    id: uuid.UUID
    file: str
    line: int
    end_line: int | None
    severity: Severity
    category: str
    message: str | None = Field(description="Null once purged after an uninstall.")
    suggestion: str | None
    confidence: float
    user_feedback: FindingFeedback | None
    dismissed: bool


class FindingUpdate(BaseModel):
    """Send `user_feedback` (or null to clear it) and/or `dismissed`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"user_feedback": "helpful"}, {"dismissed": True}]},
    )

    user_feedback: FindingFeedback | None = None
    dismissed: bool | None = None


class DiffLineOut(BaseModel):
    kind: Literal["+", "-", " "]
    old_line: int | None
    new_line: int | None
    text: str


class HunkOut(BaseModel):
    header: str
    lines: list[DiffLineOut]


class ReviewFileOut(BaseModel):
    path: str
    old_path: str | None
    status: str
    additions: int
    deletions: int
    hunks: list[HunkOut]
    patch_available: bool = Field(
        description="False when the diff was never stored, or was purged (ADR 0006)."
    )


class ReviewOut(BaseModel):
    run: RunOut
    summary: SummaryOut | None
    findings: list[FindingOut]
    files: list[ReviewFileOut]


class UsageTotals(BaseModel):
    runs: int
    succeeded: int
    failed: int
    skipped: int
    cost_usd: float = Field(description="ESTIMATED at list price (CLAUDE.md decision 8).")
    tokens_in: int
    tokens_out: int
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    findings: int
    helpful: int
    not_helpful: int
    acceptance_rate: float | None = Field(
        description="helpful / (helpful + not_helpful); null when nobody voted yet."
    )


class UsageDay(BaseModel):
    day: date
    runs: int
    cost_usd: float
    helpful: int
    not_helpful: int
    acceptance_rate: float | None


class UsageOut(BaseModel):
    date_from: date
    date_to: date
    totals: UsageTotals
    days: list[UsageDay] = Field(description="One entry per day in the range, zeros included.")
