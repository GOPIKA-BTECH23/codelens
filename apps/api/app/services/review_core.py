"""The part of a review that needs no GitHub and no database: files in, findings out.

    filter noise -> chunk -> run the pipeline (the agents) -> validate against the diff
    -> dedupe, threshold, rank, cap

The worker (app/worker/review.py) wraps this with the GitHub and bookkeeping steps; the eval
harness (evals/) calls it directly on the seeded diffs. Both run exactly this code, so the
eval numbers describe the production review, not a copy of it.

The findings are returned at three stages, because the evals measure each one:
  * raw:      everything the agents returned (schema-valid)
  * anchored: what survived validate_findings (points at real changed code)
  * selected: what would be posted (after dedupe, confidence and severity thresholds, cap)
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.ai.llm.base import LLMProvider, LLMUsage
from app.ai.pipeline import ReviewAgents
from app.ai.schemas import AgentResult, Finding
from app.schemas.dashboard import RepoSettings
from app.services import review_poster
from app.services.diff import FileDiff, FilterResult, SkippedFile, chunk_files, filter_files


@dataclass(frozen=True)
class ReviewConfig:
    """Review policy, from Settings (REVIEW_*)."""

    confidence_threshold: float = 0.6
    max_comments: int = 15
    ignore_patterns: tuple[str, ...] = ()
    max_file_changed_lines: int = 1000
    no_code_behavior: Literal["skip", "comment"] = "skip"
    chunk_max_tokens: int = 12_000
    llm_concurrency: int = 2


@dataclass(frozen=True)
class TaggedFinding:
    """A finding plus the agent that produced it (for per-agent breakdowns)."""

    agent: str
    finding: Finding


@dataclass
class ReviewOutcome:
    filtered: FilterResult
    oversized: list[SkippedFile] = field(default_factory=list)  # hunks too big for a chunk
    chunks: int = 0
    results: list[AgentResult] = field(default_factory=list)  # one per agent that ran
    raw: list[TaggedFinding] = field(default_factory=list)
    anchored: list[TaggedFinding] = field(default_factory=list)
    selected: list[TaggedFinding] = field(default_factory=list)
    counts: review_poster.DropCounts = field(default_factory=review_poster.DropCounts)

    @property
    def has_code(self) -> bool:
        return bool(self.filtered.code_files)

    @property
    def all_chunks_failed(self) -> bool:
        """True when there was something to review and no agent got a valid answer for any of
        it (the worker treats that as a permanent failure)."""
        return self.chunks > 0 and all(r.chunks_failed == r.chunks_total for r in self.results)

    @property
    def chunks_failed(self) -> int:
        return sum(r.chunks_failed for r in self.results)

    @property
    def chunks_total(self) -> int:
        """Agent-chunks: with one agent, simply the number of chunks."""
        return sum(r.chunks_total for r in self.results)

    @property
    def change_summaries(self) -> list[str]:
        return [s for r in self.results for s in r.change_summaries]


async def review_files(
    files: Sequence[FileDiff],
    pipeline: ReviewAgents,
    llm: LLMProvider,
    config: ReviewConfig,
    *,
    usage_log: list[LLMUsage],
    repo_settings: RepoSettings | None = None,
) -> ReviewOutcome:
    """Review the PR's files with `pipeline` and return the findings at every stage.

    Makes no LLM call when nothing is reviewable (`outcome.has_code` is False). Usage is
    appended to `usage_log` as each call returns; provider errors propagate.
    """
    settings = repo_settings or RepoSettings()
    filtered = filter_files(
        files,
        extra_patterns=(*config.ignore_patterns, *settings.ignore_paths),
        max_changed_lines=config.max_file_changed_lines,
    )
    outcome = ReviewOutcome(filtered=filtered)
    if not filtered.code_files:
        return outcome

    chunks, outcome.oversized = chunk_files(
        filtered.code_files, max_tokens=config.chunk_max_tokens, estimate=llm.estimate_tokens
    )
    outcome.chunks = len(chunks)
    outcome.results = await pipeline(
        chunks, llm, usage_log=usage_log, concurrency=config.llm_concurrency
    )

    for result in outcome.results:
        outcome.raw += [TaggedFinding(result.agent, f) for f in result.findings]
        valid = review_poster.validate_findings(
            result.findings, filtered.code_files, outcome.counts
        )
        outcome.anchored += [TaggedFinding(result.agent, f) for f in valid]

    # select_findings returns the same Finding objects it was given, so identity maps each
    # selected finding back to its agent.
    agent_of = {id(t.finding): t.agent for t in outcome.anchored}
    selected = review_poster.select_findings(
        [t.finding for t in outcome.anchored],
        threshold=config.confidence_threshold,
        max_comments=config.max_comments,
        counts=outcome.counts,
        min_severity=settings.severity_threshold,
    )
    outcome.selected = [TaggedFinding(agent_of[id(f)], f) for f in selected]
    return outcome
