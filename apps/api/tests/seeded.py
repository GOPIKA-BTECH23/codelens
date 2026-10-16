"""Load the seeded vulnerable PRs from evals/dataset/seeded_prs (shared with the M13 evals).

`ideal_answer` builds what a perfect model would return for a case: one finding per expected
label, with `evidence` copied from the real diff line. The FakeLLM replays it, which lets the
tests check the whole pipeline (anchoring, validation, posting) on known-correct input.

Not named test_*: pytest would collect it.
"""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from app.ai.schemas import AgentOutput, Finding
from app.services.diff import FileDiff, parse_unified_diff

SEEDED_DIR = Path(__file__).resolve().parents[3] / "evals" / "dataset" / "seeded_prs"
VULNERABLE_CASES = (
    "hardcoded_secret",
    "sql_injection",
    "unsafe_deserialization",
    "path_traversal",
    "prompt_injection",
)


@dataclass(frozen=True)
class SeededCase:
    name: str
    diff: str
    files: list[FileDiff]
    expected: list[dict[str, Any]]
    has_code_to_review: bool


@cache
def load_case(name: str) -> SeededCase:
    diff = (SEEDED_DIR / f"{name}.diff").read_text(encoding="utf-8")
    labels = json.loads((SEEDED_DIR / f"{name}.expected.json").read_text(encoding="utf-8"))
    return SeededCase(
        name=name,
        diff=diff,
        files=parse_unified_diff(diff),
        expected=labels["expected_findings"],
        has_code_to_review=labels["has_code_to_review"],
    )


def ideal_answer(case: SeededCase, confidence: float = 0.9) -> AgentOutput:
    by_path = {f.path: f for f in case.files}
    findings = [
        Finding(
            file=label["file"],
            line=label["line"],
            severity=label["min_severity"],
            category=label["category"],
            message=f"Planted {label['category']} problem.",
            suggestion="Fix it.",
            confidence=confidence,
            evidence=by_path[label["file"]].added_lines[label["line"]].strip(),
        )
        for label in case.expected
    ]
    return AgentOutput(change_summary=f"Seeded case {case.name}.", findings=findings)


def as_github_files(case: SeededCase) -> list[dict[str, Any]]:
    """The case as GitHub's "list pull request files" JSON (filename, status, patch)."""
    status = {"added": "added", "deleted": "removed", "renamed": "renamed"}
    out = []
    for f in case.files:
        patch = "\n".join(
            line for h in f.hunks for line in [h.header, *(f"{ln.kind}{ln.text}" for ln in h.lines)]
        )
        out.append(
            {
                "filename": f.path,
                "status": status.get(f.status.value, "modified"),
                "additions": f.additions,
                "deletions": f.deletions,
                "changes": f.additions + f.deletions,
                "patch": patch,
            }
        )
    return out
