"""Validate the eval dataset (or the candidate cases).

    uv run --project apps/api python -m evals.validate_dataset               # seeded_prs/
    uv run --project apps/api python -m evals.validate_dataset --candidates  # candidates/

Exit code 0 when valid, 1 with one line per problem otherwise.
"""

import argparse
import sys
from pathlib import Path

from evals.harness.dataset import (
    CANDIDATES_DIR,
    SEEDED_DIR,
    SPLITS_FILE,
    dataset_version,
    describe,
    validate_dataset,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--candidates",
        action="store_true",
        help="check evals/dataset/candidates/ (draft labels allowed, no split needed)",
    )
    parser.add_argument("--dir", type=Path, help="another case directory")
    args = parser.parse_args(argv)

    directory = args.dir or (CANDIDATES_DIR if args.candidates else SEEDED_DIR)
    splits = None if args.candidates or args.dir else SPLITS_FILE
    cases, problems = validate_dataset(directory, splits_file=splits, allow_drafts=args.candidates)
    for problem in problems:
        print(f"ERROR {problem}")
    if problems:
        print(f"{len(problems)} problem(s) in {directory}")
        return 1
    print(f"OK {directory.name}: {describe(cases)}; version {dataset_version(directory, splits)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
