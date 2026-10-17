"""Run the review pipeline on the eval dataset and write a scored result + report.

    # from the repo root (reads GROQ_API_KEY / GEMINI_API_KEY from the root .env)
    uv run --project apps/api python -m evals.run_evals --provider groq --split dev
    uv run --project apps/api python -m evals.run_evals --provider fake --split all --include-test

Exit codes: 0 complete, 2 stopped (resumable; the command to resume is printed), 1 error.
"""

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.ai.llm.factory import DEFAULT_MODELS, LLMConfigError, build_provider
from app.ai.pipeline import default_pipeline
from evals.harness.dataset import DatasetError, load_dataset
from evals.harness.matching import DEFAULT_TOLERANCE
from evals.harness.report import render_report, render_trend
from evals.harness.results import RESULTS_DIR, SMOKE_DIR, list_results
from evals.harness.runner import REPO_ROOT, RunError, RunOptions, finish, git_info, run_evals

# Free tiers limit TOKENS per minute (Groq gpt-oss-120b: 8K TPM, checked 2026-10-17) and a
# review call is ~2-3K tokens, so 3 requests a minute stays under it.
DEFAULT_RPM = 3


class EvalEnv(BaseSettings):
    """Only the LLM variables: an eval run needs no database or OAuth secrets."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", extra="ignore", hide_input_in_errors=True
    )

    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2

    @field_validator("groq_api_key", "gemini_api_key", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the CodeLens review evals.")
    p.add_argument("--provider", choices=["groq", "gemini", "fake"], required=True)
    p.add_argument("--model", help="default: the provider's default model")
    p.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    p.add_argument(
        "--include-test",
        action="store_true",
        help="required for --split test/all: confirms the run is not used to tune prompts",
    )
    p.add_argument("--cases", default="", help="comma-separated subset of case names")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--max-cost-usd", type=Decimal, default=Decimal("0.05"))
    p.add_argument("--rpm", type=int, default=DEFAULT_RPM, help="model requests per minute")
    p.add_argument("--tolerance", type=int, default=DEFAULT_TOLERANCE)
    p.add_argument("--resume", metavar="RUN_ID")
    p.add_argument(
        "--allow-dirty",
        action="store_true",
        help="run with uncommitted changes (results go to the gitignored smoke/ directory)",
    )
    p.add_argument("--out-dir", type=Path)
    return p.parse_args(argv)


async def _main(args: argparse.Namespace) -> int:
    env = EvalEnv()
    git = git_info()
    model = args.model or DEFAULT_MODELS[args.provider]
    llm = build_provider(
        args.provider,
        model,
        groq_api_key=env.groq_api_key,
        gemini_api_key=env.gemini_api_key,
        requests_per_minute=args.rpm,
        timeout_seconds=env.llm_timeout_seconds,
        max_retries=env.llm_max_retries,
    )
    smoke = args.provider == "fake" or git.dirty
    out_dir: Path = args.out_dir or (SMOKE_DIR if smoke else RESULTS_DIR)
    options = RunOptions(
        provider=args.provider,
        split=args.split,
        include_test=args.include_test,
        cases=tuple(c for c in args.cases.split(",") if c),
        repeats=args.repeats,
        max_cost_usd=args.max_cost_usd,
        tolerance=args.tolerance,
        resume=args.resume,
        allow_dirty=args.allow_dirty or args.provider == "fake",
    )
    try:
        dataset = load_dataset()
        print(f"{args.provider}/{model} on {dataset.name} v{dataset.version}, split {args.split}")
        outcome = await run_evals(
            options, dataset=dataset, llm=llm, pipeline=default_pipeline(), git=git
        )
    finally:
        await llm.aclose()

    if outcome.status == "stopped":
        print(f"STOPPED ({outcome.done}/{outcome.total} case-runs, ${outcome.spent_usd}):")
        print(outcome.message)
        return 2
    assert outcome.result is not None
    path = finish(outcome, out_dir)
    path.with_suffix(".md").write_text(
        render_report(outcome.result), encoding="utf-8", newline="\n"
    )
    if out_dir == RESULTS_DIR:
        (RESULTS_DIR / "TREND.md").write_text(
            render_trend(list_results(RESULTS_DIR)), encoding="utf-8", newline="\n"
        )
    posted = outcome.result.metrics.stages["posted"]
    print(f"done: {path}")
    print(
        f"posted: precision {posted.precision}, recall {posted.recall}, F1 {posted.f1}, "
        f"FP per clean case {posted.fp_per_clean}; estimated spend ${outcome.spent_usd}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(_main(args))
    except (RunError, DatasetError, LLMConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
