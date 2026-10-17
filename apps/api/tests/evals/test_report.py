"""Reports, the trend table, `report --check` (reproducibility) and run comparison."""

from pathlib import Path

from app.ai.llm.fake import FakeLLM
from evals.harness.report import compare_warnings, render_compare, render_trend
from evals.harness.results import RESULTS_DIR, EvalResult, load_result, write_json
from evals.harness.runner import finish
from evals.report import check_results, write_reports
from tests.evals.test_runner import false_positive_on_clean_change, run, sample_llm
from tests.seeded import ideal_answer, load_case


def as_real(result: EvalResult, run_id: str) -> EvalResult:
    """The same result as if a real provider had produced it (the trend skips fake runs)."""
    header = result.header.model_copy(update={"provider": "groq", "run_id": run_id})
    return result.model_copy(update={"header": header})


async def make_result(tmp_path: Path, llm: FakeLLM, run_id: str, **kw: object) -> Path:
    outcome = await run(tmp_path, llm, **kw)
    assert outcome.result is not None
    outcome.result = as_real(outcome.result, run_id)
    outcome.run_id = run_id
    return finish(outcome, tmp_path, tmp_path / ".partial")


def test_committed_results_are_reproducible() -> None:
    """Every committed result recomputes to its stored metrics, and every report and
    TREND.md match what the generator produces now."""
    assert check_results(RESULTS_DIR) == []


async def test_check_catches_drift(tmp_path: Path) -> None:
    path = await make_result(tmp_path, sample_llm(), "run-a")
    assert any("stale or missing" in p for p in check_results(tmp_path))
    write_reports(tmp_path)
    assert check_results(tmp_path) == []

    result = load_result(path)
    result.metrics.stages["posted"].tp += 1  # someone "improves" a number by hand
    write_json(path, result)
    assert f"{path.name}: stored metrics differ from a recomputation" in check_results(tmp_path)


async def test_trend_lists_real_runs_only(tmp_path: Path) -> None:
    path = await make_result(tmp_path, sample_llm(), "run-a")
    real = load_result(path)
    fake = real.model_copy(update={"header": real.header.model_copy(update={"provider": "fake"})})
    trend = render_trend([real, fake])
    assert trend.count("| [`run-a`](run-a.md) |") == 1
    assert "| 66.7% | 100.0% | 80.0% | 0.5 |" in trend
    assert "no runs recorded yet" in render_trend([])


async def test_compare_two_runs(tmp_path: Path) -> None:
    a = load_result(await make_result(tmp_path, sample_llm(), "run-a"))
    # B: the false positive is gone, and only one of the two SQL injections is found.
    sql = ideal_answer(load_case("sql_injection"))
    sql.findings = sql.findings[:1]
    clean = false_positive_on_clean_change()
    clean.findings = []
    b = load_result(await make_result(tmp_path, FakeLLM([clean, sql]), "run-b"))

    text = render_compare(a, b)
    assert "| posted | precision | 66.7% | 100.0% | +33.3 pts |" in text
    assert "| posted | recall | 100.0% | 50.0% | -50.0 pts |" in text
    assert "| `clean_change` | 0 / 1 / 0 | 0 / 0 / 0 |" in text
    assert "| `sql_injection` | 2 / 0 / 0 | 1 / 0 / 1 |" in text
    assert compare_warnings(a, b) == []


async def test_compare_warns_when_runs_are_not_comparable(tmp_path: Path) -> None:
    a = load_result(await make_result(tmp_path, sample_llm(), "run-a"))
    b = a.model_copy(update={"header": a.header.model_copy(update={"tolerance": 0})})
    assert compare_warnings(a, b) == [
        "different matching tolerance: the numbers are not directly comparable"
    ]
