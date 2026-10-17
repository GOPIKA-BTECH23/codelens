"""The result file: everything needed to recompute a run's numbers, and nothing secret.

A result file is self-contained on purpose. Besides the raw findings of every case-run it
stores a SNAPSHOT of the labels and of the category table it was scored with, so
`recompute(result)` gives the same metrics even after the dataset or categories.json change
later. That is what makes every number in docs/evals.md reproducible from a committed file.

Stored per finding: location, category, severity, confidence, and the model's message,
suggestion and evidence. The dataset is synthetic (no real code, no real secrets), so the
text is safe to commit; it is kept for human inspection and a future message-quality judge.
"""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.ai.schemas import Severity
from evals.harness.categories import CategoryTable, normalise
from evals.harness.dataset import IssueLabel
from evals.harness.matching import Predicted
from evals.harness.metrics import Metrics, RunInput, compute_metrics

RESULT_SCHEMA_VERSION: Literal[1] = 1
RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
PARTIAL_DIR = RESULTS_DIR / ".partial"  # gitignored: in-progress runs (resumable)
SMOKE_DIR = RESULTS_DIR / "smoke"  # gitignored: fake-provider runs

Stage = Literal["raw", "anchored", "posted"]


class FindingRecord(BaseModel):
    agent: str
    stage: Stage  # the furthest stage it reached: posted implies anchored implies raw
    file: str
    line: int
    end_line: int | None
    severity: Severity
    category: str
    confidence: float
    message: str
    suggestion: str | None
    evidence: str

    def predicted(self) -> Predicted:
        return Predicted(
            agent=self.agent,
            file=self.file,
            line=self.line,
            end_line=self.end_line,
            category=self.category,
            severity=self.severity,
            confidence=self.confidence,
        )


class CaseRun(BaseModel):
    """One case reviewed once."""

    case: str
    repeat: int
    has_code: bool
    files_reviewed: int
    chunks_total: int
    chunks_failed: int
    calls: int
    invalid_outputs: int
    tokens_in: int
    tokens_out: int
    cost_usd: str  # estimated, at list price, of the attempt that finished
    retry_cost_usd: str = "0"  # attempts that died on a transient error (spent all the same)
    attempts: int = 1
    model_latency_ms: int
    wall_ms: int
    drop_counts: dict[str, int]
    findings: list[FindingRecord]


class LabelSnapshot(BaseModel):
    split: str | None
    clean: bool
    has_code_to_review: bool
    expected_findings: list[IssueLabel]
    optional_findings: list[IssueLabel]


class RunHeader(BaseModel):
    """What identifies a run. Resuming requires every field except run_id and created_at
    to be unchanged."""

    run_id: str
    created_at: datetime
    git_sha: str
    git_dirty: bool
    provider: str
    model: str
    pipeline: str
    prompt_versions: list[str]
    dataset_name: str
    dataset_version: str
    splits_status: str
    split: str
    include_test: bool
    cases: list[str]
    repeats: int
    tolerance: int
    categories_sha256: str
    review_config: dict[str, Any]

    def fingerprint(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"run_id", "created_at"})


class Budget(BaseModel):
    max_cost_usd: str
    spent_usd: str


class EvalResult(BaseModel):
    schema_version: Literal[1] = RESULT_SCHEMA_VERSION
    header: RunHeader
    finished_at: datetime
    budget: Budget
    labels: dict[str, LabelSnapshot]
    categories: dict[str, Any] = Field(description="categories.json as used for scoring")
    runs: list[CaseRun]
    metrics: Metrics

    @property
    def is_fake(self) -> bool:
        return self.header.provider == "fake"


def table_from_snapshot(data: dict[str, Any], sha256: str) -> CategoryTable:
    families = {
        fam: frozenset({fam, *(normalise(a) for a in aliases)})
        for fam, aliases in data["families"].items()
    }
    umbrellas = {term: frozenset(m) for term, m in data.get("umbrellas", {}).items()}
    return CategoryTable(families, umbrellas, sha256)


def run_inputs(labels: dict[str, LabelSnapshot], runs: list[CaseRun]) -> list[RunInput]:
    inputs = []
    for run in runs:
        snap = labels[run.case]
        stages: dict[str, list[Predicted]] = {"raw": [], "anchored": [], "posted": []}
        for f in run.findings:
            reached = {"raw": ["raw"], "anchored": ["raw", "anchored"]}.get(f.stage, list(stages))
            for stage in reached:
                stages[stage].append(f.predicted())
        inputs.append(
            RunInput(
                case=run.case,
                split=snap.split,
                repeat=run.repeat,
                clean=snap.clean,
                has_code=snap.has_code_to_review,
                issues=snap.expected_findings,
                optional=snap.optional_findings,
                stages=stages,
                calls=run.calls,
                invalid_outputs=run.invalid_outputs,
                chunks_total=run.chunks_total,
                chunks_failed=run.chunks_failed,
                cost_usd=Decimal(run.cost_usd),
                model_latency_ms=run.model_latency_ms,
                wall_ms=run.wall_ms,
            )
        )
    return inputs


def score(
    labels: dict[str, LabelSnapshot], runs: list[CaseRun], table: CategoryTable, tolerance: int
) -> Metrics:
    return compute_metrics(run_inputs(labels, runs), table, tolerance)


def recompute(result: EvalResult) -> Metrics:
    """The metrics again, from nothing but the file's own findings and snapshots."""
    table = table_from_snapshot(result.categories, result.header.categories_sha256)
    return score(result.labels, result.runs, table, result.header.tolerance)


def load_result(path: Path) -> EvalResult:
    return EvalResult.model_validate_json(path.read_text(encoding="utf-8"))


def write_json(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def list_results(directory: Path = RESULTS_DIR) -> list[EvalResult]:
    """Every result file in `directory` (not in .partial/ or smoke/), oldest first."""
    found = [load_result(p) for p in sorted(directory.glob("*.json"))]
    return sorted(found, key=lambda r: r.header.created_at)
