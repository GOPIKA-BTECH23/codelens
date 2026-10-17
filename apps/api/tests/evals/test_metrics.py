"""Metric computation against hand-computed examples (the arithmetic is in the comments)."""

from decimal import Decimal

import pytest

from app.ai.schemas import Severity
from evals.harness.categories import load_categories
from evals.harness.dataset import IssueLabel
from evals.harness.matching import Predicted
from evals.harness.metrics import RunInput, compute_metrics, ratio, stage_metrics, wilson

CATS = load_categories()


def issue(line: int, category: str = "sql-injection") -> IssueLabel:
    return IssueLabel(file="a.py", line=line, category=category, min_severity=Severity.HIGH)


def pred(line: int, category: str = "sql-injection", agent: str = "security") -> Predicted:
    return Predicted(agent, "a.py", line, None, category, Severity.HIGH, 0.9)


def run(
    case: str,
    issues: list[IssueLabel],
    posted: list[Predicted],
    *,
    raw: list[Predicted] | None = None,
    has_code: bool = True,
    repeat: int = 1,
    split: str = "dev",
    **usage: object,
) -> RunInput:
    raw = posted if raw is None else raw
    return RunInput(
        case=case,
        split=split,
        repeat=repeat,
        clean=not issues,
        has_code=has_code,
        issues=issues,
        optional=[],
        stages={"raw": raw, "anchored": posted, "posted": posted},
        **usage,  # type: ignore[arg-type]
    )


# Case A: 2 issues; findings: a hit (alias), a duplicate of it, a wrong-category FP.
# Case B: clean, with code; one FP with a category the table does not know.
# Case C: clean, docs only; never reviewed.
RUNS = [
    run(
        "a",
        [issue(10), issue(20)],
        [pred(10, "sqli"), pred(11), pred(50, "xss")],
        calls=2,
        invalid_outputs=1,
        chunks_total=1,
        cost_usd=Decimal("0.002"),
        model_latency_ms=1000,
        wall_ms=1500,
    ),
    run(
        "b",
        [],
        [pred(3, "weird-thing")],
        calls=1,
        chunks_total=1,
        cost_usd=Decimal("0.001"),
        model_latency_ms=500,
        wall_ms=700,
        split="test",
    ),
    run("c", [], [], has_code=False),
]


def test_headline_numbers() -> None:
    m = compute_metrics(RUNS, CATS, tolerance=2).stages["posted"]
    # TP = 1 (a:10). FP = duplicate a:11 + xss a:50 + b:3 = 3. FN = a:20 = 1.
    assert (m.tp, m.fp, m.fn, m.duplicates) == (1, 3, 1, 1)
    assert m.precision == 0.25  # 1 / (1 + 3)
    assert m.recall == 0.5  # 1 / (1 + 1)
    assert m.f1 == 0.3333  # 2*1 / (2*1 + 3 + 1) = 2/6
    assert m.recall_ci95 == (0.0945, 0.9055)  # textbook Wilson interval for 1 of 2
    # Clean case-runs: b (1 FP) and c (0). With code: only b.
    assert (m.clean_runs, m.fp_on_clean, m.fp_per_clean) == (2, 1, 0.5)
    assert (m.clean_with_code_runs, m.fp_per_clean_with_code) == (1, 1.0)
    assert m.severity_agreement == 1.0


def test_usage_cost_and_latency() -> None:
    u = compute_metrics(RUNS, CATS, tolerance=2).usage
    assert (u.calls, u.invalid_outputs, u.invalid_output_rate) == (3, 1, 0.3333)  # 1 of 3
    assert u.reviewed_runs == 2  # c made no model call
    assert u.total_cost_usd == "0.003"
    assert u.cost_per_case_usd == "0.001000"  # 0.003 / 3 case-runs
    assert u.cost_per_reviewed_case_usd == "0.001500"  # 0.003 / 2
    assert u.model_latency_ms_per_case == 500.0  # 1500 / 3
    assert u.model_latency_ms_per_reviewed_case == 750.0  # 1500 / 2
    assert u.wall_ms_per_case == 733.3  # 2200 / 3
    assert u.max_wall_ms == 1500


def test_breakdowns() -> None:
    m = compute_metrics(RUNS, CATS, tolerance=2)
    rows = {r.category: r for r in m.per_category}
    # sql-injection: 2 issues, 1 found. Predicted as that family: the hit and the duplicate.
    assert (rows["sql-injection"].recall, rows["sql-injection"].precision) == (0.5, 0.5)
    assert (rows["xss"].predicted, rows["xss"].correct, rows["xss"].known) == (1, 0, True)
    assert rows["weird-thing"].known is False
    assert m.unknown_categories == {"weird-thing": 1}
    assert [(a.agent, a.tp, a.fp) for a in m.per_agent] == [("security", 1, 3)]
    assert m.per_split["dev"].recall == 0.5 and m.per_split["test"].fp == 1
    assert [(c.case, c.tp, c.fp, c.fn) for c in m.per_case] == [
        ("a", 1, 2, 1),
        ("b", 0, 1, 0),
        ("c", 0, 0, 0),
    ]
    assert (m.sample.cases, m.sample.issues, m.sample.clean, m.sample.clean_with_code) == (
        3,
        2,
        2,
        1,
    )


def test_stages_are_scored_separately() -> None:
    runs = [run("a", [issue(10)], posted=[], raw=[pred(10), pred(40)])]
    m = compute_metrics(runs, CATS, tolerance=2)
    assert (m.stages["raw"].tp, m.stages["raw"].fp) == (1, 1)
    assert (m.stages["posted"].tp, m.stages["posted"].fn) == (0, 1)
    assert m.stages["posted"].precision is None  # nothing posted: undefined, not 0 or 1
    assert m.stages["posted"].f1 == 0.0  # 0 / (0 + 0 + 1)


def test_strict_tolerance_row() -> None:
    m = compute_metrics([run("a", [issue(10)], [pred(12)])], CATS, tolerance=2)
    assert m.stages["posted"].tp == 1 and m.strict_posted.tp == 0


def test_repeats_pool_counts_and_keep_the_dataset_sized_interval() -> None:
    runs = [run("a", [issue(10)], [pred(10)], repeat=1), run("a", [issue(10)], [], repeat=2)]
    m = stage_metrics(runs, "posted", CATS, 2)
    assert m.recall == 0.5
    assert [(r.repeat, r.recall) for r in m.per_repeat] == [(1, 1.0), (2, 0.0)]
    # n is the per-repeat denominator (1 issue), not 2: repeating does not shrink the interval.
    assert m.recall_ci95 == wilson(0.5, 1)


def test_ratio_and_wilson_edges() -> None:
    assert ratio(1, 0) is None
    assert wilson(None, 5) is None and wilson(0.5, 0) is None
    low, high = wilson(1.0, 8) or (0, 0)
    assert high == 1.0 and low == pytest.approx(0.6756, abs=1e-4)  # 8 of 8
