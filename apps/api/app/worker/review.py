"""The real review_pr pipeline (M5): fetch -> filter -> agent -> validate -> post -> persist.

The handler in handlers.py has already checked the run (exists, not finished, not
superseded, installation installed, under the cost cap) and moved it to `running`. This
module does the work and ends the run as `succeeded` or `skipped`, or raises:
  * PermanentJobError for what a retry cannot fix (no access to the repo, bad LLM key, the
    model failing on every chunk), so the run is failed at once;
  * anything else (rate limits, outages) to let the job queue retry with backoff.

Idempotency (the queue is at-least-once, see M4):
  * Before any LLM call we look for a review that THIS run already posted (hidden marker in
    the body). If there is one, the previous attempt died after posting: just finish.
  * Findings are saved BEFORE posting, so the rows always match what was posted.
  * LLM usage is ADDED to the run after every attempt, even a failed one.

Order of the steps, and why:
    1. PR still open and still at the commit the webhook saw? (else skip: stale)
    2. Already posted by an earlier attempt? (else continue)
    3. List files, filter noise. Nothing reviewable? skip, or post a short note (config).
    4. Chunk, run the security agent; record usage whatever happens.
    5. Validate findings against the diff, dedupe, threshold, cap.
    6. Save findings + head_sha.
    7. Re-check the run is still `running` (a newer push may have superseded it, decision 16).
    8. Post ONE review; mark succeeded with latency and the GitHub review id.
"""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.agents.security import PROMPT_NAME, PROMPT_VERSION, security_agent
from app.ai.llm.base import LLMProvider, LLMRequestRejected, LLMUsage, total_usage
from app.ai.prompts import load_prompt
from app.db.models.enums import RunStatus
from app.services import review_poster, runs
from app.services.diff import chunk_files, filter_files, from_pr_files
from app.services.github_app import GitHubApp
from app.services.github_client import (
    GitHubClient,
    GitHubForbidden,
    GitHubNotFound,
    GitHubUnauthorized,
    ReviewRequest,
    installation_client,
)
from app.worker.errors import PermanentJobError

logger = logging.getLogger("app.worker.review")


class ReviewPipeline(Protocol):
    """What the review_pr handler calls once the run is `running`."""

    async def __call__(self, run_id: uuid.UUID, head_sha: str | None) -> None: ...


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


ClientFactory = Callable[[int], GitHubClient]


class GitHubReviewPipeline:
    """The production pipeline. `client_for(installation_id)` builds the GitHub client, so
    tests can hand in one that talks to a mocked GitHub."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMProvider,
        config: ReviewConfig,
        client_for: ClientFactory,
    ) -> None:
        self._session_factory = session_factory
        self._llm = llm
        self._config = config
        self._client_for = client_for
        self._prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION)

    async def __call__(self, run_id: uuid.UUID, head_sha: str | None) -> None:
        try:
            await self._review(run_id, head_sha)
        except (GitHubNotFound, GitHubForbidden, GitHubUnauthorized) as exc:
            raise PermanentJobError(f"GitHub refused access: {exc}") from exc
        except LLMRequestRejected as exc:
            raise PermanentJobError(f"LLM provider rejected the request: {exc}") from exc

    async def _review(self, run_id: uuid.UUID, head_sha: str | None) -> None:
        started = time.monotonic()
        async with self._session_factory() as session:
            target = await runs.load_review_target(session, run_id)
        if target is None:
            raise PermanentJobError(f"review run {run_id} does not exist")
        gh = self._client_for(target.github_installation_id)
        repo, number = target.repo_full_name, target.pr_number

        # 1. Is the PR still what the webhook described?
        pr = await gh.get_pull_request(repo, number)
        if pr.state != "open":
            await self._skip(run_id, "pull request is no longer open")
            return
        if head_sha is not None and pr.head.sha != head_sha:
            # A newer push exists; its own webhook queues (and supersedes us with) a new run.
            await self._skip(run_id, "stale: the pull request has newer commits")
            return
        head = pr.head.sha

        # 2. Did an earlier attempt of this run already post? Then only the bookkeeping died.
        posted = await review_poster.find_posted_review(gh, repo, number, run_id)
        if posted is not None:
            logger.info("review already posted by an earlier attempt", extra={"review": posted})
            await self._succeed(run_id, started, github_review_id=posted)
            return

        # 3. What is there to review?
        files = from_pr_files(await gh.list_pull_request_files(repo, number))
        filtered = filter_files(
            files,
            extra_patterns=self._config.ignore_patterns,
            max_changed_lines=self._config.max_file_changed_lines,
        )
        summary = review_poster.ReviewSummary(
            files_reviewed=len(filtered.code_files),
            skipped=list(filtered.skipped),
            doc_files=len(filtered.doc_files),
        )
        if not filtered.code_files:
            await self._no_code(run_id, gh, repo, number, head, summary, started)
            return

        # 4. The agent. Usage is recorded even if it raises.
        chunks, oversized = chunk_files(
            filtered.code_files,
            max_tokens=self._config.chunk_max_tokens,
            estimate=self._llm.estimate_tokens,
        )
        summary.skipped.extend(oversized)
        usage_log: list[LLMUsage] = []
        try:
            result = await security_agent(
                chunks,
                self._llm,
                usage_log=usage_log,
                concurrency=self._config.llm_concurrency,
                prompt=self._prompt,
            )
        finally:
            await self._record_usage(run_id, usage_log)
        if chunks and result.chunks_failed == len(chunks):
            raise PermanentJobError(
                f"the model returned invalid output for all {len(chunks)} part(s) of the diff"
            )

        # 5. Keep only findings that point at real changed code.
        counts = review_poster.DropCounts()
        valid = review_poster.validate_findings(result.findings, filtered.code_files, counts)
        selected = review_poster.select_findings(
            valid,
            threshold=self._config.confidence_threshold,
            max_comments=self._config.max_comments,
            counts=counts,
        )
        summary.chunks_total = result.chunks_total
        summary.chunks_failed = result.chunks_failed
        summary.change_summaries = result.change_summaries
        summary.counts = counts
        summary.model = result.model
        summary.prompt_version = result.prompt_version
        logger.info(
            "findings validated",
            extra={
                "raw": len(result.findings),
                "posted": len(selected),
                "not_anchored": counts.not_anchored,
                "low_confidence": counts.low_confidence,
                "duplicates": counts.duplicate,
                "over_cap": counts.over_cap,
            },
        )

        # 6. Save what we are about to post.
        async with self._session_factory.begin() as session:
            await runs.replace_findings(session, run_id, head, selected)

        # 7 + 8. Post, unless a newer push superseded us meanwhile.
        review = review_poster.build_review(run_id, head, selected, summary)
        await self._post_and_succeed(run_id, gh, repo, number, review, started)

    # --- steps ---------------------------------------------------------------------------------

    async def _no_code(
        self,
        run_id: uuid.UUID,
        gh: GitHubClient,
        repo: str,
        number: int,
        head: str,
        summary: review_poster.ReviewSummary,
        started: float,
    ) -> None:
        reason = "no code changes to review (only docs, lockfiles, generated or binary files)"
        if self._config.no_code_behavior == "skip":
            await self._skip(run_id, reason)
            return
        async with self._session_factory.begin() as session:
            await runs.replace_findings(session, run_id, head, [])
        review = review_poster.build_review(run_id, head, [], summary)
        await self._post_and_succeed(run_id, gh, repo, number, review, started)

    async def _post_and_succeed(
        self,
        run_id: uuid.UUID,
        gh: GitHubClient,
        repo: str,
        number: int,
        review: ReviewRequest,
        started: float,
    ) -> None:
        async with self._session_factory() as session:
            info = await runs.load_run(session, run_id)
        if info is None or info.status != RunStatus.RUNNING:
            logger.info(
                "run changed state before posting (superseded?); nothing posted",
                extra={"status": info.status if info else None},
            )
            return
        review_id = await review_poster.post_review(gh, repo, number, run_id, review)
        logger.info("review posted", extra={"review": review_id, "comments": len(review.comments)})
        await self._succeed(run_id, started, github_review_id=review_id)

    async def _succeed(self, run_id: uuid.UUID, started: float, **values: Any) -> None:
        latency_ms = int((time.monotonic() - started) * 1000)
        async with self._session_factory.begin() as session:
            if not await runs.mark_succeeded(session, run_id, latency_ms=latency_ms, **values):
                logger.info("run changed state while finishing (superseded?)")

    async def _skip(self, run_id: uuid.UUID, reason: str) -> None:
        async with self._session_factory.begin() as session:
            await runs.mark_skipped(session, run_id, reason)
        logger.info("review skipped: %s", reason)

    async def _record_usage(self, run_id: uuid.UUID, usage_log: list[LLMUsage]) -> None:
        if not usage_log:
            return
        totals = total_usage(usage_log)
        async with self._session_factory.begin() as session:
            await runs.add_usage(
                session, run_id, totals, model=self._llm.model, prompt_version=self._prompt.id
            )
        logger.info(
            "llm usage recorded",
            extra={
                "calls": totals.calls,
                "tokens_in": totals.tokens_in,
                "tokens_out": totals.tokens_out,
                "estimated_cost_usd": str(totals.cost_usd),
            },
        )


def installation_clients(
    app: GitHubApp, http: httpx.AsyncClient, **kwargs: Any
) -> Callable[[int], GitHubClient]:
    """The production ClientFactory: one installation-token client per installation."""

    def build(installation_id: int) -> GitHubClient:
        return installation_client(app, installation_id, http, **kwargs)

    return build
