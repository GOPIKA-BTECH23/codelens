"""Metrics over scored case-runs. Pure functions: no I/O, no model calls.

Definitions (docs/evals.md explains the choices):
  * precision = TP / (TP + FP),  recall = TP / (TP + FN),  F1 = 2TP / (2TP + FP + FN).
    Counts are POOLED over every case-run (micro-average): with one or two issues per case,
    per-case averages would swing wildly. Undefined values (0/0) are None, never 0 or 1.
  * 95% Wilson score intervals for precision and recall. With repeats, the interval uses the
    per-repeat denominator (repeats reduce model-sampling noise, not the fact that the
    dataset has only so many issues), so it is not artificially narrowed by repeating.
  * false positives per clean case-run, for all clean cases and for clean cases that contain
    code (a docs-only PR never reaches the model, so its 0 is guaranteed by the filter).
  * invalid-output rate = invalid structured answers / model calls that returned.
  * cost and latency per case-run, over all case-runs and over those that called the model.
"""

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel

from evals.harness.categories import CategoryTable
from evals.harness.dataset import IssueLabel
from evals.harness.matching import CaseMatch, Predicted, match_case

STAGES = ("raw", "anchored", "posted")  # model output -> anchored to the diff -> what is posted
HEADLINE_STAGE = "posted"
Z_95 = 1.959964
_DIGITS = 4


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, _DIGITS)


def ratio(k: float, n: float) -> float | None:
    return None if n == 0 else k / n


def wilson(p: float | None, n: float, z: float = Z_95) -> tuple[float, float] | None:
    """Wilson score interval for a proportion p observed on n trials (n may be fractional:
    an average denominator)."""
    if p is None or n <= 0:
        return None
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (round(max(0.0, centre - half), _DIGITS), round(min(1.0, centre + half), _DIGITS))


@dataclass(frozen=True)
class RunInput:
    """One case reviewed once (a case-run), as the metrics need it."""

    case: str
    split: str | None
    repeat: int
    clean: bool
    has_code: bool
    issues: Sequence[IssueLabel]
    optional: Sequence[IssueLabel]
    stages: Mapping[str, Sequence[Predicted]]  # "raw" | "anchored" | "posted"
    calls: int = 0
    invalid_outputs: int = 0
    chunks_total: int = 0
    chunks_failed: int = 0
    cost_usd: Decimal = Decimal(0)
    model_latency_ms: int = 0
    wall_ms: int = 0


# --- result models (stored in the result file, so they must round-trip through JSON) ---------


class RepeatScore(BaseModel):
    repeat: int
    precision: float | None
    recall: float | None
    f1: float | None


class StageMetrics(BaseModel):
    tp: int
    fp: int  # duplicates included
    fn: int
    duplicates: int
    optional_hits: int
    precision: float | None
    recall: float | None
    f1: float | None
    precision_ci95: tuple[float, float] | None
    recall_ci95: tuple[float, float] | None
    severity_agreement: float | None  # share of hits with severity >= the label's minimum
    clean_runs: int
    fp_on_clean: int
    fp_per_clean: float | None
    clean_with_code_runs: int
    fp_on_clean_with_code: int
    fp_per_clean_with_code: float | None
    per_repeat: list[RepeatScore]


class CategoryRow(BaseModel):
    category: str
    known: bool  # False: not in categories.json (a possibly missing alias)
    issues: int
    found: int
    recall: float | None
    predicted: int
    correct: int
    precision: float | None


class AgentRow(BaseModel):
    agent: str
    findings: int
    tp: int
    fp: int
    precision: float | None


class CaseRow(BaseModel):
    case: str
    split: str | None
    runs: int
    issues: int  # per run
    tp: int  # summed over runs
    fp: int
    fn: int
    duplicates: int
    optional_hits: int


class Sample(BaseModel):
    cases: int
    with_issues: int
    clean: int
    clean_with_code: int
    issues: int
    optional: int
    repeats: int
    case_runs: int


class Usage(BaseModel):
    calls: int
    invalid_outputs: int
    invalid_output_rate: float | None
    chunks_total: int
    chunks_failed: int
    chunk_failure_rate: float | None
    reviewed_runs: int  # case-runs that called the model
    total_cost_usd: str  # Decimal as text: estimated at list price
    cost_per_case_usd: str | None
    cost_per_reviewed_case_usd: str | None
    model_latency_ms_per_case: float | None
    model_latency_ms_per_reviewed_case: float | None
    wall_ms_per_case: float | None
    wall_ms_per_reviewed_case: float | None
    max_wall_ms: int


class Metrics(BaseModel):
    tolerance: int
    categories_sha256: str
    sample: Sample
    stages: dict[str, StageMetrics]
    strict_posted: StageMetrics  # the headline stage again at tolerance 0
    per_split: dict[str, StageMetrics]
    per_category: list[CategoryRow]
    per_agent: list[AgentRow]
    per_case: list[CaseRow]
    unknown_categories: dict[str, int]  # raw-stage categories not in categories.json
    usage: Usage


# --- computation ------------------------------------------------------------------------------


def _match(run: RunInput, stage: str, categories: CategoryTable, tolerance: int) -> CaseMatch:
    return match_case(
        run.stages[stage],
        run.issues,
        run.optional,
        categories=categories,
        tolerance=tolerance,
    )


def stage_metrics(
    runs: Sequence[RunInput], stage: str, categories: CategoryTable, tolerance: int
) -> StageMetrics:
    matches = [(run, _match(run, stage, categories, tolerance)) for run in runs]
    tp = sum(m.tp for _, m in matches)
    fp = sum(m.fp for _, m in matches)
    fn = sum(m.fn for _, m in matches)
    sev_ok = sum(1 for _, m in matches for j in m.judged if j.severity_ok)
    repeats = sorted({run.repeat for run in runs}) or [1]
    precision, recall = ratio(tp, tp + fp), ratio(tp, tp + fn)

    clean = [(r, m) for r, m in matches if r.clean]
    clean_code = [(r, m) for r, m in clean if r.has_code]
    fp_clean = sum(m.fp for _, m in clean)
    fp_clean_code = sum(m.fp for _, m in clean_code)

    per_repeat = []
    for rep in repeats:
        sub = [m for r, m in matches if r.repeat == rep]
        k_tp, k_fp, k_fn = sum(m.tp for m in sub), sum(m.fp for m in sub), sum(m.fn for m in sub)
        per_repeat.append(
            RepeatScore(
                repeat=rep,
                precision=_r(ratio(k_tp, k_tp + k_fp)),
                recall=_r(ratio(k_tp, k_tp + k_fn)),
                f1=_r(ratio(2 * k_tp, 2 * k_tp + k_fp + k_fn)),
            )
        )
    return StageMetrics(
        tp=tp,
        fp=fp,
        fn=fn,
        duplicates=sum(m.count("duplicate") for _, m in matches),
        optional_hits=sum(m.count("optional") for _, m in matches),
        precision=_r(precision),
        recall=_r(recall),
        f1=_r(ratio(2 * tp, 2 * tp + fp + fn)),
        precision_ci95=wilson(precision, (tp + fp) / len(repeats)),
        recall_ci95=wilson(recall, (tp + fn) / len(repeats)),
        severity_agreement=_r(ratio(sev_ok, tp)),
        clean_runs=len(clean),
        fp_on_clean=fp_clean,
        fp_per_clean=_r(ratio(fp_clean, len(clean))),
        clean_with_code_runs=len(clean_code),
        fp_on_clean_with_code=fp_clean_code,
        fp_per_clean_with_code=_r(ratio(fp_clean_code, len(clean_code))),
        per_repeat=per_repeat,
    )


def _per_category(
    runs: Sequence[RunInput], categories: CategoryTable, tolerance: int
) -> list[CategoryRow]:
    issues: Counter[str] = Counter()
    found: Counter[str] = Counter()
    predicted: Counter[str] = Counter()
    correct: Counter[str] = Counter()
    for run in runs:
        m = _match(run, HEADLINE_STAGE, categories, tolerance)
        issues.update(i.category for i in run.issues)
        found.update(
            run.issues[j.issue].category
            for j in m.judged
            if j.outcome == "tp" and j.issue is not None
        )
        for j in m.judged:
            if j.outcome == "optional":
                continue
            family = categories.family_of(j.prediction.category) or j.prediction.category
            predicted[family] += 1
            correct[family] += j.outcome == "tp"
    rows = []
    for name in sorted(set(issues) | set(predicted)):
        rows.append(
            CategoryRow(
                category=name,
                known=name in categories.families,
                issues=issues[name],
                found=found[name],
                recall=_r(ratio(found[name], issues[name])),
                predicted=predicted[name],
                correct=correct[name],
                precision=_r(ratio(correct[name], predicted[name])),
            )
        )
    return rows


def _per_agent(
    runs: Sequence[RunInput], categories: CategoryTable, tolerance: int
) -> list[AgentRow]:
    tally: dict[str, Counter[str]] = defaultdict(Counter)
    for run in runs:
        for j in _match(run, HEADLINE_STAGE, categories, tolerance).judged:
            tally[j.prediction.agent][j.outcome] += 1
    rows = []
    for agent in sorted(tally):
        c = tally[agent]
        fp = c["fp"] + c["duplicate"]
        rows.append(
            AgentRow(
                agent=agent,
                findings=sum(c.values()),
                tp=c["tp"],
                fp=fp,
                precision=_r(ratio(c["tp"], c["tp"] + fp)),
            )
        )
    return rows


def _per_case(runs: Sequence[RunInput], categories: CategoryTable, tolerance: int) -> list[CaseRow]:
    rows: dict[str, CaseRow] = {}
    for run in runs:
        m = _match(run, HEADLINE_STAGE, categories, tolerance)
        row = rows.setdefault(
            run.case,
            CaseRow(
                case=run.case,
                split=run.split,
                runs=0,
                issues=len(run.issues),
                tp=0,
                fp=0,
                fn=0,
                duplicates=0,
                optional_hits=0,
            ),
        )
        row.runs += 1
        row.tp += m.tp
        row.fp += m.fp
        row.fn += m.fn
        row.duplicates += m.count("duplicate")
        row.optional_hits += m.count("optional")
    return [rows[name] for name in sorted(rows)]


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _mean_cost(values: Sequence[Decimal]) -> str | None:
    return (
        str((sum(values, Decimal(0)) / len(values)).quantize(Decimal("0.000001")))
        if values
        else None
    )


def _usage(runs: Sequence[RunInput]) -> Usage:
    reviewed = [r for r in runs if r.calls > 0]
    calls = sum(r.calls for r in runs)
    invalid = sum(r.invalid_outputs for r in runs)
    chunks = sum(r.chunks_total for r in runs)
    failed = sum(r.chunks_failed for r in runs)
    return Usage(
        calls=calls,
        invalid_outputs=invalid,
        invalid_output_rate=_r(ratio(invalid, calls)),
        chunks_total=chunks,
        chunks_failed=failed,
        chunk_failure_rate=_r(ratio(failed, chunks)),
        reviewed_runs=len(reviewed),
        total_cost_usd=str(sum((r.cost_usd for r in runs), Decimal(0))),
        cost_per_case_usd=_mean_cost([r.cost_usd for r in runs]),
        cost_per_reviewed_case_usd=_mean_cost([r.cost_usd for r in reviewed]),
        model_latency_ms_per_case=_mean([r.model_latency_ms for r in runs]),
        model_latency_ms_per_reviewed_case=_mean([r.model_latency_ms for r in reviewed]),
        wall_ms_per_case=_mean([r.wall_ms for r in runs]),
        wall_ms_per_reviewed_case=_mean([r.wall_ms for r in reviewed]),
        max_wall_ms=max((r.wall_ms for r in runs), default=0),
    )


def _sample(runs: Sequence[RunInput]) -> Sample:
    cases = {r.case: r for r in runs}.values()
    clean = [r for r in cases if r.clean]
    return Sample(
        cases=len(cases),
        with_issues=len(cases) - len(clean),
        clean=len(clean),
        clean_with_code=sum(1 for r in clean if r.has_code),
        issues=sum(len(r.issues) for r in cases),
        optional=sum(len(r.optional) for r in cases),
        repeats=len({r.repeat for r in runs}),
        case_runs=len(runs),
    )


def compute_metrics(runs: Sequence[RunInput], categories: CategoryTable, tolerance: int) -> Metrics:
    splits = sorted({r.split for r in runs if r.split is not None})
    unknown = Counter(
        p.category for r in runs for p in r.stages["raw"] if not categories.is_known(p.category)
    )
    return Metrics(
        tolerance=tolerance,
        categories_sha256=categories.sha256,
        sample=_sample(runs),
        stages={s: stage_metrics(runs, s, categories, tolerance) for s in STAGES},
        strict_posted=stage_metrics(runs, HEADLINE_STAGE, categories, 0),
        per_split={
            s: stage_metrics(
                [r for r in runs if r.split == s], HEADLINE_STAGE, categories, tolerance
            )
            for s in splits
        },
        per_category=_per_category(runs, categories, tolerance),
        per_agent=_per_agent(runs, categories, tolerance),
        per_case=_per_case(runs, categories, tolerance),
        unknown_categories=dict(sorted(unknown.items())),
        usage=_usage(runs),
    )
