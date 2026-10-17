"""Run the review pipeline over the dataset, resumably, under a cost cap.

Order of work: repeat-major (every case of repeat 1, then repeat 2, ...), one case at a
time, chunks one at a time. Free tiers limit tokens per minute (Groq: 8K TPM for
gpt-oss-120b on 2026-10-17, console.groq.com/docs/rate-limits), so being slow is the point.

After every finished case-run, one JSON line is appended to .partial/<run_id>.jsonl. A run
that stops (rate limited after retries, outage, budget cap, Ctrl+C) loses at most the case in
flight; `--resume <run_id>` continues where it stopped. Resuming refuses when anything that
changes the numbers is different (commit, model, prompts, dataset, matching config...).

Error policy per case:
  * LLMRateLimited / LLMUnavailable: back off and retry the case (BACKOFF_SECONDS), then
    stop the run, resumable. The SDK has already retried each call itself.
  * LLMRequestRejected (bad key, unknown model): stop at once; retrying cannot help.
  * Anything else is a bug and propagates.
"""

import asyncio
import json
import subprocess
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from app.ai.llm.base import (
    LLMProvider,
    LLMRateLimited,
    LLMRequestRejected,
    LLMUnavailable,
    LLMUsage,
    total_usage,
)
from app.ai.pipeline import ReviewAgents
from app.services.review_core import ReviewConfig, review_files
from evals.harness.categories import CATEGORIES_FILE, load_categories
from evals.harness.dataset import Case, Dataset
from evals.harness.matching import DEFAULT_TOLERANCE
from evals.harness.results import (
    PARTIAL_DIR,
    Budget,
    CaseRun,
    EvalResult,
    FindingRecord,
    LabelSnapshot,
    RunHeader,
    Stage,
    score,
    write_json,
)

BACKOFF_SECONDS = (30.0, 90.0)  # waits before the 2nd and 3rd attempt of a case
REPO_ROOT = Path(__file__).resolve().parents[2]
# Production review policy, except one chunk at a time (rate limits).
EVAL_REVIEW_CONFIG = ReviewConfig(llm_concurrency=1)


@dataclass(frozen=True)
class GitInfo:
    sha: str
    dirty: bool


def git_info(root: Path = REPO_ROOT) -> GitInfo:
    """HEAD and whether the tree has uncommitted changes. Changes under evals/results/ do
    not count: writing results must not make the next run "dirty"."""

    def git(*args: str) -> str:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
        return out.stdout.strip()

    status = git("status", "--porcelain", "--", ".", ":(exclude)evals/results")
    return GitInfo(sha=git("rev-parse", "HEAD"), dirty=bool(status))


@dataclass(frozen=True)
class RunOptions:
    provider: str
    split: Literal["dev", "test", "all"] = "dev"
    include_test: bool = False
    cases: tuple[str, ...] = ()
    repeats: int = 1
    max_cost_usd: Decimal = Decimal("0.05")
    tolerance: int = DEFAULT_TOLERANCE
    resume: str | None = None
    allow_dirty: bool = False
    review_config: ReviewConfig = EVAL_REVIEW_CONFIG
    categories_file: Path = CATEGORIES_FILE


@dataclass
class RunOutcome:
    status: Literal["complete", "stopped"]
    run_id: str
    message: str
    result: EvalResult | None = None
    done: int = 0
    total: int = 0
    spent_usd: Decimal = field(default_factory=lambda: Decimal(0))


class RunError(ValueError):
    """The run cannot start (bad options, or a resume that does not fit)."""


def select_cases(dataset: Dataset, options: RunOptions) -> list[Case]:
    if options.split in ("test", "all") and not options.include_test:
        raise RunError(
            f"--split {options.split} includes the held-out test split; pass --include-test "
            "to confirm this run is not used for tuning"
        )
    cases = dataset.select(options.split, options.cases)
    missing = set(options.cases) - {c.name for c in cases}
    if missing:
        raise RunError(f"not in split {options.split}: {', '.join(sorted(missing))}")
    if not cases:
        raise RunError("no cases selected")
    return cases


def make_header(
    options: RunOptions,
    *,
    dataset: Dataset,
    cases: Sequence[Case],
    llm: LLMProvider,
    pipeline: ReviewAgents,
    git: GitInfo,
    now: datetime,
) -> RunHeader:
    prompts = list(pipeline.prompt_versions)
    dirty = "-dirty" if git.dirty else ""
    return RunHeader(
        run_id=f"{now:%Y%m%dT%H%M%SZ}-{git.sha[:7]}{dirty}-{'+'.join(prompts)}",
        created_at=now,
        git_sha=git.sha,
        git_dirty=git.dirty,
        provider=options.provider,
        model=llm.model,
        pipeline=pipeline.name,
        prompt_versions=prompts,
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        splits_status=dataset.splits_status,
        split=options.split,
        include_test=options.include_test,
        cases=[c.name for c in cases],
        repeats=options.repeats,
        tolerance=options.tolerance,
        categories_sha256=load_categories(options.categories_file).sha256,
        review_config=asdict(options.review_config),
    )


# --- one case ------------------------------------------------------------------------------


async def review_case(
    case: Case,
    repeat: int,
    pipeline: ReviewAgents,
    llm: LLMProvider,
    config: ReviewConfig,
    usage_log: list[LLMUsage],
) -> CaseRun:
    started = time.monotonic()
    outcome = await review_files(case.files, pipeline, llm, config, usage_log=usage_log)
    wall_ms = int((time.monotonic() - started) * 1000)

    stage_of: dict[int, Stage] = {id(t.finding): "raw" for t in outcome.raw}
    stage_of.update({id(t.finding): "anchored" for t in outcome.anchored})
    stage_of.update({id(t.finding): "posted" for t in outcome.selected})
    totals = total_usage(usage_log)
    return CaseRun(
        case=case.name,
        repeat=repeat,
        has_code=outcome.has_code,
        files_reviewed=len(outcome.filtered.code_files),
        chunks_total=outcome.chunks_total,
        chunks_failed=outcome.chunks_failed,
        calls=sum(r.calls for r in outcome.results),
        invalid_outputs=sum(r.invalid_outputs for r in outcome.results),
        tokens_in=totals.tokens_in,
        tokens_out=totals.tokens_out,
        cost_usd=str(totals.cost_usd),
        model_latency_ms=totals.latency_ms,
        wall_ms=wall_ms,
        drop_counts=asdict(outcome.counts),
        findings=[
            FindingRecord(
                agent=t.agent,
                stage=stage_of[id(t.finding)],
                file=t.finding.file,
                line=t.finding.line,
                end_line=t.finding.end_line,
                severity=t.finding.severity,
                category=t.finding.category,
                confidence=t.finding.confidence,
                message=t.finding.message,
                suggestion=t.finding.suggestion,
                evidence=t.finding.evidence,
            )
            for t in outcome.raw
        ],
    )


# --- the partial (checkpoint) file -----------------------------------------------------------


def _read_partial(path: Path) -> tuple[RunHeader, list[CaseRun]]:
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    header = RunHeader.model_validate(lines[0]["header"])
    # Other lines are {"note": ...} (why a run stopped), kept for humans reading the file.
    return header, [CaseRun.model_validate(line["run"]) for line in lines[1:] if "run" in line]


def _append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _start_or_resume(
    options: RunOptions, fresh: RunHeader, partial_dir: Path
) -> tuple[RunHeader, list[CaseRun], Path]:
    if options.resume is None:
        path = partial_dir / f"{fresh.run_id}.jsonl"
        _append(path, {"header": fresh.model_dump(mode="json")})
        return fresh, [], path
    path = partial_dir / f"{options.resume}.jsonl"
    if not path.is_file():
        raise RunError(f"nothing to resume: {path} does not exist")
    header, done = _read_partial(path)
    old, new = header.fingerprint(), fresh.fingerprint()
    changed = sorted(k for k in old if old[k] != new.get(k))
    if changed:
        raise RunError(f"cannot resume {options.resume}: changed since it started: {changed}")
    return header, done, path


def _spent(runs: Sequence[CaseRun]) -> Decimal:
    return sum((Decimal(r.cost_usd) + Decimal(r.retry_cost_usd) for r in runs), Decimal(0))


# --- the run ---------------------------------------------------------------------------------

Sleep = Callable[[float], Awaitable[None]]


async def run_evals(
    options: RunOptions,
    *,
    dataset: Dataset,
    llm: LLMProvider,
    pipeline: ReviewAgents,
    git: GitInfo,
    partial_dir: Path = PARTIAL_DIR,
    sleep: Sleep = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC).replace(microsecond=0),
    log: Callable[[str], None] = print,
) -> RunOutcome:
    """Review every selected case `repeats` times. Returns `complete` with the scored
    EvalResult, or `stopped` with a message saying how to resume."""
    if git.dirty and not options.allow_dirty:
        raise RunError(
            "the working tree has uncommitted changes outside evals/results/: commit first so "
            "the result's git sha describes the code that produced it (or pass --allow-dirty "
            "for a throwaway run)"
        )
    cases = select_cases(dataset, options)
    fresh = make_header(
        options, dataset=dataset, cases=cases, llm=llm, pipeline=pipeline, git=git, now=now()
    )
    header, done, partial = _start_or_resume(options, fresh, partial_dir)
    finished = {(r.case, r.repeat) for r in done}
    plan = [(rep, c) for rep in range(1, options.repeats + 1) for c in cases]

    def stopped(message: str) -> RunOutcome:
        return RunOutcome(
            "stopped",
            header.run_id,
            f"{message}\nResume with: --resume {header.run_id}",
            done=len(done),
            total=len(plan),
            spent_usd=_spent(done),
        )

    for repeat, case in plan:
        if (case.name, repeat) in finished:
            continue
        if _spent(done) >= options.max_cost_usd:
            return stopped(
                f"budget reached: spent ${_spent(done)} of ${options.max_cost_usd} "
                f"(estimated); {len(done)}/{len(plan)} case-runs done"
            )
        retry_cost = Decimal(0)
        for attempt in range(1, len(BACKOFF_SECONDS) + 2):
            usage_log: list[LLMUsage] = []
            try:
                run = await review_case(
                    case, repeat, pipeline, llm, options.review_config, usage_log
                )
            except LLMRequestRejected as exc:
                return stopped(f"the provider rejected the request (not retried): {exc}")
            except (LLMRateLimited, LLMUnavailable) as exc:
                retry_cost += total_usage(usage_log).cost_usd
                if attempt > len(BACKOFF_SECONDS):
                    _append(partial, {"note": f"{case.name}#{repeat}: gave up: {exc}"})
                    return stopped(f"{case.name} (repeat {repeat}) failed {attempt} times: {exc}")
                wait = BACKOFF_SECONDS[attempt - 1]
                log(f"  {case.name}#{repeat}: {type(exc).__name__}, retrying in {wait:.0f}s")
                await sleep(wait)
                continue
            run.attempts, run.retry_cost_usd = attempt, str(retry_cost)
            break
        done.append(run)
        _append(partial, {"run": run.model_dump(mode="json")})
        log(
            f"  [{len(done)}/{len(plan)}] {case.name}#{repeat}: {run.calls} call(s), "
            f"{len(run.findings)} finding(s), ${run.cost_usd}"
        )

    categories = json.loads(options.categories_file.read_text(encoding="utf-8"))
    table = load_categories(options.categories_file)
    labels = {
        c.name: LabelSnapshot(
            split=c.split,
            clean=c.labels.clean,
            has_code_to_review=c.labels.has_code_to_review,
            expected_findings=c.issues,
            optional_findings=c.optional,
        )
        for c in cases
    }
    ordered = sorted(done, key=lambda r: (r.repeat, r.case))
    result = EvalResult(
        header=header,
        finished_at=now(),
        budget=Budget(max_cost_usd=str(options.max_cost_usd), spent_usd=str(_spent(done))),
        labels=labels,
        categories=categories,
        runs=ordered,
        metrics=score(labels, ordered, table, header.tolerance),
    )
    return RunOutcome(
        "complete",
        header.run_id,
        "complete",
        result=result,
        done=len(done),
        total=len(plan),
        spent_usd=_spent(done),
    )


def finish(outcome: RunOutcome, out_dir: Path, partial_dir: Path = PARTIAL_DIR) -> Path:
    """Write the result file and drop the checkpoint. Returns the result path."""
    assert outcome.result is not None
    path = out_dir / f"{outcome.run_id}.json"
    write_json(path, outcome.result)
    (partial_dir / f"{outcome.run_id}.jsonl").unlink(missing_ok=True)
    return path
