"""Diff two eval runs (for example two prompt versions) as markdown.

uv run --project apps/api python -m evals.compare evals/results/A.json evals/results/B.json
... --out comparison.md   # also write it to a file
"""

import argparse
import sys
from pathlib import Path

from evals.harness.report import render_compare
from evals.harness.results import load_result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("a", type=Path, help="baseline result file")
    parser.add_argument("b", type=Path, help="candidate result file")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    text = render_compare(load_result(args.a), load_result(args.b))
    if args.out:
        args.out.write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
