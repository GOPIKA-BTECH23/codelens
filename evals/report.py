"""Regenerate (or check) the markdown reports and TREND.md from the committed result files.

    uv run --project apps/api python -m evals.report           # rewrite every .md
    uv run --project apps/api python -m evals.report --check   # CI: fail if anything drifted

--check recomputes every result's metrics from its own stored findings and label snapshot
and fails if they differ from the stored metrics, or if a report or TREND.md is stale. That
is the guarantee behind "every number in docs/evals.md is reproducible from a result file".
"""

import argparse
import sys
from pathlib import Path

from evals.harness.report import render_report, render_trend
from evals.harness.results import RESULTS_DIR, list_results, load_result, recompute


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.is_file() else None


def check_results(directory: Path = RESULTS_DIR) -> list[str]:
    problems = []
    for path in sorted(directory.glob("*.json")):
        result = load_result(path)
        if recompute(result) != result.metrics:
            problems.append(f"{path.name}: stored metrics differ from a recomputation")
        if _read(path.with_suffix(".md")) != render_report(result):
            problems.append(f"{path.with_suffix('.md').name} is stale or missing")
    if _read(directory / "TREND.md") != render_trend(list_results(directory)):
        problems.append("TREND.md is stale or missing")
    return problems


def write_reports(directory: Path = RESULTS_DIR) -> None:
    for path in sorted(directory.glob("*.json")):
        _write(path.with_suffix(".md"), render_report(load_result(path)))
    _write(directory / "TREND.md", render_trend(list_results(directory)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)
    if args.check:
        problems = check_results(args.dir)
        for problem in problems:
            print(f"ERROR {problem}")
        print("results OK" if not problems else "run: python -m evals.report")
        return 1 if problems else 0
    write_reports(args.dir)
    print(f"reports written in {args.dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
