"""The harness end to end with the FakeLLM on a 3-case sample, plus resume, budget and the
guards that keep runs honest."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.ai.llm.base import LLMRateLimited, LLMRequestRejected
from app.ai.llm.fake import FakeLLM
from app.ai.pipeline import default_pipeline
from app.ai.schemas import AgentOutput, Finding
from evals.harness.dataset import Dataset, load_dataset
from evals.harness.report import render_report
from evals.harness.results import load_result, recompute
from evals.harness.runner import GitInfo, RunError, RunOptions, RunOutcome, finish, run_evals
from tests.seeded import ideal_answer, load_case

SAMPLE = ("clean_change", "docs_only", "sql_injection")  # run in this (name) order
CLEAN = GitInfo(sha="0123456789abcdef", dirty=False)


def false_positive_on_clean_change() -> AgentOutput:
    return AgentOutput(
        change_summary="Adds a lookup.",
        findings=[
            Finding(
                file="app/users.py",
                line=10,
                severity="medium",
                category="sql-injection",
                message="Looks like SQL built from input.",
                confidence=0.7,
                evidence='"SELECT id, email FROM users WHERE username = %s", (username,)',
            )
        ],
    )


def sample_llm() -> FakeLLM:
    # clean_change: 1 call, a confident false positive. docs_only: no call (filtered).
    # sql_injection: one invalid answer, then the ideal one (2 hits).
    return FakeLLM(
        [false_positive_on_clean_change(), "not json", ideal_answer(load_case("sql_injection"))],
        price_as="openai/gpt-oss-120b",  # non-zero estimated cost
    )


def options(**kw: object) -> RunOptions:
    base: dict[str, object] = {"provider": "fake", "split": "all", "include_test": True}
    return RunOptions(cases=SAMPLE, **{**base, **kw})  # type: ignore[arg-type]


async def no_sleep(_: float) -> None:
    return None


async def run(
    tmp_path: Path, llm: FakeLLM, dataset: Dataset | None = None, **kw: object
) -> RunOutcome:
    return await run_evals(
        options(**kw),
        dataset=dataset or load_dataset(),
        llm=llm,
        pipeline=default_pipeline(),
        git=CLEAN,
        partial_dir=tmp_path / ".partial",
        sleep=no_sleep,
        now=lambda: datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
        log=lambda _: None,
    )


async def test_three_case_sample_end_to_end(tmp_path: Path) -> None:
    outcome = await run(tmp_path, sample_llm())
    assert outcome.status == "complete"
    path = finish(outcome, tmp_path, tmp_path / ".partial")
    result = load_result(path)
    m = result.metrics
    posted = m.stages["posted"]

    assert result.header.run_id == "20261017T120000Z-0123456-security_v1"
    assert path.name == f"{result.header.run_id}.json"
    # 2 hits on sql_injection, 1 false positive on clean_change, nothing missed.
    assert (posted.tp, posted.fp, posted.fn) == (2, 1, 0)
    assert (posted.precision, posted.recall, posted.f1) == (0.6667, 1.0, 0.8)  # 2/3, 2/2, 4/5
    assert posted.fp_per_clean == 0.5  # 1 FP over 2 clean cases (docs_only scores 0)
    assert posted.fp_per_clean_with_code == 1.0
    # Calls: clean_change 1 + sql_injection 2 (one invalid) = 3.
    assert (m.usage.calls, m.usage.invalid_outputs, m.usage.invalid_output_rate) == (3, 1, 0.3333)
    assert m.usage.reviewed_runs == 2 and Decimal(m.usage.total_cost_usd) > 0
    assert m.sample.cases == 3 and m.sample.issues == 2
    assert recompute(result) == m  # the file alone reproduces its numbers
    assert not (tmp_path / ".partial" / f"{result.header.run_id}.jsonl").exists()

    report = render_report(result)
    assert "3 cases (1 with issues, 2 clean [1 with code]), 2 required issues" in report
    assert "| Precision | 66.7%" in report and "| Recall | 100.0%" in report


async def test_stopped_run_resumes_without_redoing_finished_cases(tmp_path: Path) -> None:
    # clean_change succeeds; sql_injection is rate limited on all 3 attempts.
    first = FakeLLM([false_positive_on_clean_change(), *[LLMRateLimited("429")] * 3])
    stopped = await run(tmp_path, first)
    assert stopped.status == "stopped"
    assert "--resume 20261017T120000Z-0123456-security_v1" in stopped.message

    second = FakeLLM([ideal_answer(load_case("sql_injection"))])
    resumed = await run(tmp_path, second, resume=stopped.run_id)
    assert resumed.status == "complete"
    assert len(second.calls) == 1  # only sql_injection ran again
    assert resumed.result is not None
    posted = resumed.result.metrics.stages["posted"]
    assert (posted.tp, posted.fp) == (2, 1)
    sql = next(r for r in resumed.result.runs if r.case == "sql_injection")
    assert sql.attempts == 1  # the resumed run's own attempt count


async def test_transient_error_is_retried_within_the_run(tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            false_positive_on_clean_change(),
            LLMRateLimited("429"),
            ideal_answer(load_case("sql_injection")),
        ]
    )
    outcome = await run(tmp_path, llm)
    assert outcome.status == "complete" and outcome.result is not None
    sql = next(r for r in outcome.result.runs if r.case == "sql_injection")
    assert sql.attempts == 2


async def test_resume_refuses_when_the_setup_changed(tmp_path: Path) -> None:
    stopped = await run(
        tmp_path, FakeLLM([false_positive_on_clean_change(), *[LLMRateLimited("x")] * 3])
    )
    with pytest.raises(RunError, match="tolerance"):
        await run(tmp_path, FakeLLM(), resume=stopped.run_id, tolerance=0)


async def test_budget_stops_before_the_next_case(tmp_path: Path) -> None:
    outcome = await run(tmp_path, sample_llm(), max_cost_usd=Decimal("0.0000001"))
    assert outcome.status == "stopped"
    assert "budget reached" in outcome.message
    assert outcome.done == 1  # clean_change ran, then the cap hit


async def test_rejected_request_stops_without_retrying(tmp_path: Path) -> None:
    llm = FakeLLM([LLMRequestRejected("bad key")])
    outcome = await run(tmp_path, llm)
    assert outcome.status == "stopped" and len(llm.calls) == 1


async def test_test_split_needs_explicit_consent(tmp_path: Path) -> None:
    with pytest.raises(RunError, match="--include-test"):
        await run(tmp_path, FakeLLM(), include_test=False)


async def test_dirty_tree_is_refused_unless_allowed(tmp_path: Path) -> None:
    with pytest.raises(RunError, match="uncommitted"):
        await run_evals(
            options(),
            dataset=load_dataset(),
            llm=FakeLLM(),
            pipeline=default_pipeline(),
            git=GitInfo("abc", dirty=True),
            partial_dir=tmp_path,
        )
