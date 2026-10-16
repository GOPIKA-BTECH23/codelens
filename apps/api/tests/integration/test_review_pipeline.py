"""The real review_pr pipeline end to end: worker + real Postgres + FakeLLM + a fake GitHub.

The job goes through the queue, the M4 handler wrapper and the M5 pipeline, exactly like in
production. Only the two outside services are replaced: GitHub (respx; it rejects comments
on lines outside the diff, like the real API) and the model (FakeLLM, scripted).
"""

import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.llm.base import LLMRateLimited, Message, StructuredResult
from app.ai.llm.fake import FakeLLM
from app.ai.schemas import AgentOutput, Finding, Severity
from app.db.models.accounts import Installation
from app.db.models.enums import JobStatus, RunStatus
from app.db.models.repos import Finding as FindingRow
from app.db.models.repos import PullRequest, Repository, ReviewRun, ReviewRunFile
from app.services import runs
from app.services.github_app import GitHubApp
from app.services.jobs import PostgresJobQueue
from app.services.review_poster import run_marker
from app.services.webhooks import JOB_REVIEW_PR
from app.worker.review import GitHubReviewPipeline, ReviewConfig, installation_clients
from tests.app_utils import TEST_CLIENT_ID
from tests.fake_github_app import FakeGitHubApp, FakePR
from tests.integration.test_worker import (
    enqueue,
    get_run,
    job_is,
    job_row,
    make_worker,
    running,
    wait_for,
)
from tests.keys import app_private_key
from tests.seeded import VULNERABLE_CASES, as_github_files, ideal_answer, load_case

Factory = async_sessionmaker[AsyncSession]
REPO = "demo-owner/codelens-demo"
PR_NUMBER = 2
INSTALLATION = 3003
HEAD = "c" * 40
MODEL = "openai/gpt-oss-120b"  # the FakeLLM reports (and is priced as) the default model


@pytest.fixture
def queue(session_factory: Factory) -> PostgresJobQueue:
    return PostgresJobQueue(session_factory, backoff_base=timedelta(milliseconds=20))


@pytest.fixture
def github() -> Iterator[FakeGitHubApp]:
    with respx.mock(assert_all_called=False) as mock:
        yield FakeGitHubApp(mock, INSTALLATION)


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


async def _no_sleep(_: float) -> None:
    return None


def pipeline(
    factory: Factory, llm: Any, http: httpx.AsyncClient, **config: Any
) -> GitHubReviewPipeline:
    app = GitHubApp(http, client_id=TEST_CLIENT_ID, private_key=app_private_key())
    return GitHubReviewPipeline(
        factory, llm, ReviewConfig(**config), installation_clients(app, http, sleep=_no_sleep)
    )


async def make_run(factory: Factory, settings: dict[str, Any] | None = None) -> uuid.UUID:
    async with factory.begin() as session:
        inst = Installation(
            github_installation_id=INSTALLATION, account_login="demo-owner", account_type="user"
        )
        session.add(inst)
        await session.flush()
        repo = Repository(
            installation_id=inst.id, github_repo_id=2002, full_name=REPO, settings=settings or {}
        )
        session.add(repo)
        await session.flush()
        pr = PullRequest(repository_id=repo.id, number=PR_NUMBER, title="t", author_login="a")
        session.add(pr)
        await session.flush()
        run = ReviewRun(pull_request_id=pr.id)
        session.add(run)
        await session.flush()
        return run.id


async def review(
    factory: Factory,
    queue: PostgresJobQueue,
    pipe: GitHubReviewPipeline,
    *,
    head_sha: str = HEAD,
    expect: JobStatus = JobStatus.SUCCEEDED,
    max_attempts: int = 5,
    settings: dict[str, Any] | None = None,
) -> uuid.UUID:
    """Create a run, enqueue its job, run the worker until the job reaches `expect`."""
    run_id = await make_run(factory, settings)
    job_id = await enqueue(
        queue,
        factory,
        JOB_REVIEW_PR,
        {"review_run_id": str(run_id), "head_sha": head_sha},
        max_attempts=max_attempts,
    )
    async with running(make_worker(factory, queue, pipeline=pipe)):
        await wait_for(job_is(factory, job_id, expect))
    return run_id


def seeded_pr(*cases: str) -> FakePR:
    files = [f for name in cases for f in as_github_files(load_case(name))]
    return FakePR(REPO, PR_NUMBER, HEAD, files)


def ideal(*cases: str) -> list[Finding]:
    return [f for name in cases for f in ideal_answer(load_case(name)).findings]


async def finding_rows(factory: Factory, run_id: uuid.UUID) -> list[FindingRow]:
    async with factory() as session:
        return list(
            (
                await session.scalars(select(FindingRow).where(FindingRow.review_run_id == run_id))
            ).all()
        )


# --- the happy path -----------------------------------------------------------------------------


async def test_seeded_vulnerable_pr_gets_one_review_with_comments_on_the_right_lines(
    session_factory: Factory,
    queue: PostgresJobQueue,
    github: FakeGitHubApp,
    http: httpx.AsyncClient,
) -> None:
    pr = github.add(seeded_pr(*VULNERABLE_CASES))
    expected = ideal(*VULNERABLE_CASES)
    hallucinated = expected[0].model_copy(update={"line": 99})  # not in the diff
    elsewhere = expected[0].model_copy(update={"file": "app/not_in_this_pr.py"})
    # Correctly anchored, but a guess: filtered by the confidence threshold, not dropped.
    unsure = expected[1].model_copy(update={"category": "info-leak", "confidence": 0.3})
    answer = AgentOutput(
        change_summary="Adds config, queries, sessions, downloads and image helpers.",
        findings=[*expected, hallucinated, elsewhere, unsure],
    )
    llm = FakeLLM([answer], model=MODEL, price_as=MODEL)

    run_id = await review(session_factory, queue, pipeline(session_factory, llm, http))

    # GitHub got exactly ONE review: a COMMENT, anchored on the planted lines only.
    assert len(pr.posted) == 1
    posted = pr.posted[0]
    assert posted["event"] == "COMMENT" and posted["commit_id"] == HEAD
    assert {(c["path"], c["line"]) for c in posted["comments"]} == {
        (f.file, f.line) for f in expected
    }
    assert posted["body"].startswith(run_marker(run_id))
    assert "8 inline comments: 8 high." in posted["body"]
    assert "2 finding(s) were dropped" in posted["body"]  # hallucinated line + unknown file
    assert "1 lower-confidence finding(s) are not shown" in posted["body"]
    assert "AI-generated" in posted["body"]

    # The database records the run, its cost and the posted findings.
    run = await get_run(session_factory, run_id)
    assert run.status == RunStatus.SUCCEEDED and run.error is None
    assert run.model == MODEL and run.prompt_version == "security_v1"
    assert run.tokens_in and run.tokens_in > 0 and run.tokens_out and run.tokens_out > 0
    assert run.cost_usd is not None and run.cost_usd > Decimal(0)
    assert run.latency_ms is not None and run.latency_ms >= 0
    assert run.head_sha == HEAD and run.github_review_id == 1000
    rows = await finding_rows(session_factory, run_id)
    assert sorted((r.file, r.line, r.category) for r in rows) == sorted(
        (f.file, f.line, f.category) for f in expected
    )
    assert all(r.message and r.confidence == 0.9 for r in rows)
    assert github.token_requests == 1  # one installation token, cached for the whole review

    # M6: the dashboard's summary and the reviewed files' patches are stored with the run.
    assert run.summary is not None
    assert (
        run.summary["what_changed"]
        == "Adds config, queries, sessions, downloads and image helpers."
    )
    assert any("Reviewed" in n for n in run.summary["notes"])
    async with session_factory() as s:
        stored = (
            await s.scalars(select(ReviewRunFile).where(ReviewRunFile.review_run_id == run_id))
        ).all()
    assert {f.path for f in stored} == {f.file for f in expected}
    assert all(f.patch and f.patch.startswith("@@ ") for f in stored)


async def test_clean_change_posts_a_no_issues_summary(
    session_factory: Factory,
    queue: PostgresJobQueue,
    github: FakeGitHubApp,
    http: httpx.AsyncClient,
) -> None:
    pr = github.add(seeded_pr("clean_change"))
    llm = FakeLLM([AgentOutput(change_summary="Parameterises a query.", findings=[])])
    run_id = await review(session_factory, queue, pipeline(session_factory, llm, http))
    assert pr.posted[0]["comments"] == []
    assert "No security issues found" in pr.posted[0]["body"]
    assert (await get_run(session_factory, run_id)).status == RunStatus.SUCCEEDED


async def test_obeying_an_injection_still_cannot_approve(
    session_factory: Factory,
    queue: PostgresJobQueue,
    github: FakeGitHubApp,
    http: httpx.AsyncClient,
) -> None:
    pr = github.add(seeded_pr("prompt_injection"))
    fooled = {"change_summary": "Approved, ping @maintainers", "findings": [], "event": "APPROVE"}
    llm = FakeLLM([fooled])
    await review(session_factory, queue, pipeline(session_factory, llm, http))
    assert pr.posted[0]["event"] == "COMMENT"
    assert "@maintainers" not in pr.posted[0]["body"]
    # What the model saw: the injected comment only inside the delimited data block.
    user = llm.calls[0].messages[1].content
    assert "approve this PR" in user.split("<<<UNTRUSTED_DIFF_")[2]


# --- nothing to review --------------------------------------------------------------------------


async def test_docs_only_pr_is_skipped_by_default_without_calling_the_model(
    session_factory: Factory,
    queue: PostgresJobQueue,
    github: FakeGitHubApp,
    http: httpx.AsyncClient,
) -> None:
    pr = github.add(seeded_pr("docs_only"))
    llm = FakeLLM()
    run_id = await review(session_factory, queue, pipeline(session_factory, llm, http))
    run = await get_run(session_factory, run_id)
    assert run.status == RunStatus.SKIPPED
    assert run.error is not None and "no code changes" in run.error
    assert llm.calls == [] and pr.posted == []


async def test_docs_only_pr_gets_a_short_note_when_configured(
    session_factory: Factory,
    queue: PostgresJobQueue,
    github: FakeGitHubApp,
    http: httpx.AsyncClient,
) -> None:
    pr = github.add(seeded_pr("docs_only"))
    llm = FakeLLM()
    pipe = pipeline(session_factory, llm, http, no_code_behavior="comment")
    run_id = await review(session_factory, queue, pipe)
    assert llm.calls == []
    assert pr.posted[0]["comments"] == []
    assert "2 documentation file(s) were not security-reviewed" in pr.posted[0]["body"]
    run = await get_run(session_factory, run_id)
    assert run.status == RunStatus.SUCCEEDED and run.tokens_in is None


# --- idempotency and staleness ------------------------------------------------------------------


async def test_a_retried_job_does_not_post_twice_or_call_the_model_again(
    session_factory: Factory,
    queue: PostgresJobQueue,
    github: FakeGitHubApp,
    http: httpx.AsyncClient,
) -> None:
    """Simulates "the worker died right after GitHub accepted the review": the review with
    this run's marker already exists, so the retry only finishes the bookkeeping."""
    pr = github.add(seeded_pr("sql_injection"))
    run_id = await make_run(session_factory)
    pr.reviews.append(
        {"id": 555, "body": f"{run_marker(run_id)}\nalready posted", "commit_id": HEAD}
    )
    llm = FakeLLM()
    job_id = await enqueue(
        queue, session_factory, JOB_REVIEW_PR, {"review_run_id": str(run_id), "head_sha": HEAD}
    )
    async with running(
        make_worker(session_factory, queue, pipeline=pipeline(session_factory, llm, http))
    ):
        await wait_for(job_is(session_factory, job_id, JobStatus.SUCCEEDED))

    run = await get_run(session_factory, run_id)
    assert run.status == RunStatus.SUCCEEDED and run.github_review_id == 555
    assert llm.calls == [] and pr.posted == []


async def test_stale_head_is_skipped(
    session_factory: Factory,
    queue: PostgresJobQueue,
    github: FakeGitHubApp,
    http: httpx.AsyncClient,
) -> None:
    pr = github.add(seeded_pr("sql_injection"))
    llm = FakeLLM()
    run_id = await review(
        session_factory, queue, pipeline(session_factory, llm, http), head_sha="0" * 40
    )
    run = await get_run(session_factory, run_id)
    assert run.status == RunStatus.SKIPPED and "newer commits" in (run.error or "")
    assert llm.calls == [] and pr.posted == []


class SupersedingLLM:
    """Answers like the FakeLLM, but a "newer push" supersedes the run while it thinks."""

    def __init__(self, factory: Factory, inner: FakeLLM) -> None:
        self.factory, self.inner = factory, inner
        self.name, self.model = inner.name, inner.model
        self.run_id: uuid.UUID | None = None

    async def generate_structured[T: BaseModel](
        self, schema: type[T], messages: Sequence[Message], *, max_output_tokens: int = 4096
    ) -> StructuredResult[T]:
        async with self.factory.begin() as session:
            run = (await session.scalars(select(ReviewRun))).one()
            run.status = RunStatus.SUPERSEDED
        return await self.inner.generate_structured(schema, messages)

    def estimate_tokens(self, text: str) -> int:
        return self.inner.estimate_tokens(text)


async def test_superseded_while_reviewing_posts_nothing(
    session_factory: Factory,
    queue: PostgresJobQueue,
    github: FakeGitHubApp,
    http: httpx.AsyncClient,
) -> None:
    pr = github.add(seeded_pr("sql_injection"))
    llm = SupersedingLLM(session_factory, FakeLLM([ideal_answer(load_case("sql_injection"))]))
    run_id = await review(session_factory, queue, pipeline(session_factory, llm, http))
    assert pr.posted == []  # decision 16: re-check right before posting
    run = await get_run(session_factory, run_id)
    assert run.status == RunStatus.SUPERSEDED
    assert run.tokens_in and run.tokens_in > 0  # the spend is still recorded


# --- failures -----------------------------------------------------------------------------------


async def test_model_failing_on_every_chunk_fails_the_run_but_records_the_spend(
    session_factory: Factory,
    queue: PostgresJobQueue,
    github: FakeGitHubApp,
    http: httpx.AsyncClient,
) -> None:
    pr = github.add(seeded_pr("sql_injection"))
    llm = FakeLLM(["not json", "{}", '{"findings": "nope"}'], model=MODEL, price_as=MODEL)
    run_id = await review(
        session_factory, queue, pipeline(session_factory, llm, http), expect=JobStatus.DEAD
    )
    run = await get_run(session_factory, run_id)
    assert run.status == RunStatus.FAILED and "invalid output" in (run.error or "")
    assert len(llm.calls) == 3 and pr.posted == []
    assert run.tokens_in and run.tokens_in > 0 and run.cost_usd and run.cost_usd > 0


async def test_rate_limited_model_is_retried_by_the_queue(
    session_factory: Factory,
    queue: PostgresJobQueue,
    github: FakeGitHubApp,
    http: httpx.AsyncClient,
) -> None:
    pr = github.add(seeded_pr("sql_injection"))
    llm = FakeLLM([LLMRateLimited("429"), ideal_answer(load_case("sql_injection"))])
    run_id = await review(session_factory, queue, pipeline(session_factory, llm, http))
    assert len(pr.posted) == 1 and len(pr.posted[0]["comments"]) == 2
    async with session_factory() as session:
        info = await runs.load_run(session, run_id)
    assert info is not None and info.status == RunStatus.SUCCEEDED


async def test_github_refusing_access_is_permanent(
    session_factory: Factory,
    queue: PostgresJobQueue,
    github: FakeGitHubApp,
    http: httpx.AsyncClient,
) -> None:
    github.add(seeded_pr("sql_injection"))
    github.fail_pr_with = 404
    run_id = await review(
        session_factory, queue, pipeline(session_factory, FakeLLM(), http), expect=JobStatus.DEAD
    )
    run = await get_run(session_factory, run_id)
    assert run.status == RunStatus.FAILED and "GitHub refused access" in (run.error or "")


async def test_job_attempts_are_not_wasted_on_permanent_errors(
    session_factory: Factory,
    queue: PostgresJobQueue,
    github: FakeGitHubApp,
    http: httpx.AsyncClient,
) -> None:
    github.add(seeded_pr("sql_injection"))
    github.fail_pr_with = 403
    run_id = await make_run(session_factory)
    job_id = await enqueue(
        queue, session_factory, JOB_REVIEW_PR, {"review_run_id": str(run_id), "head_sha": HEAD}
    )
    async with running(
        make_worker(session_factory, queue, pipeline=pipeline(session_factory, FakeLLM(), http))
    ):
        await wait_for(job_is(session_factory, job_id, JobStatus.DEAD))
    assert (await job_row(session_factory, job_id)).attempts == 1


# --- per-repository settings (M6) ----------------------------------------------------------------


async def test_severity_threshold_setting_hides_lower_findings_and_says_so(
    session_factory: Factory,
    queue: PostgresJobQueue,
    github: FakeGitHubApp,
    http: httpx.AsyncClient,
) -> None:
    pr = github.add(seeded_pr("sql_injection"))
    expected = ideal("sql_injection")
    low = expected[0].model_copy(
        update={"severity": Severity.LOW, "category": "weak-hash", "line": expected[0].line}
    )
    llm = FakeLLM([AgentOutput(change_summary="x", findings=[*expected, low])])
    run_id = await review(
        session_factory,
        queue,
        pipeline(session_factory, llm, http),
        settings={"severity_threshold": "high"},
    )
    posted = pr.posted[0]
    assert {c["path"] for c in posted["comments"]} == {f.file for f in expected}
    assert len(posted["comments"]) == len(expected)  # the low one is gone
    assert "below this repository's severity threshold" in posted["body"]
    rows = await finding_rows(session_factory, run_id)
    assert all(r.severity in ("high", "critical") for r in rows)


async def test_ignore_paths_setting_removes_files_from_the_review(
    session_factory: Factory,
    queue: PostgresJobQueue,
    github: FakeGitHubApp,
    http: httpx.AsyncClient,
) -> None:
    case = load_case("sql_injection")
    pr = github.add(seeded_pr("sql_injection"))
    llm = FakeLLM()
    run_id = await review(
        session_factory,
        queue,
        pipeline(session_factory, llm, http),
        settings={"ignore_paths": [f.path for f in case.files]},
    )
    run = await get_run(session_factory, run_id)
    assert run.status == RunStatus.SKIPPED and "no code changes" in (run.error or "")
    assert llm.calls == [] and pr.posted == []


async def test_disabled_security_agent_skips_the_run_before_any_work(
    session_factory: Factory,
    queue: PostgresJobQueue,
    github: FakeGitHubApp,
    http: httpx.AsyncClient,
) -> None:
    pr = github.add(seeded_pr("sql_injection"))
    llm = FakeLLM()
    run_id = await review(
        session_factory,
        queue,
        pipeline(session_factory, llm, http),
        settings={"enabled_agents": []},
    )
    run = await get_run(session_factory, run_id)
    assert run.status == RunStatus.SKIPPED and "disabled" in (run.error or "")
    assert llm.calls == [] and pr.posted == [] and github.token_requests == 0
