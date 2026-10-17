"""The matching rule: when does a reported finding count as finding a labelled issue?

A finding MATCHES an issue when all three hold:
  1. same file (exact path),
  2. the finding's lines [line, end_line] are within `tolerance` lines of the issue's lines
     [line, end_line] (0 = they overlap; default tolerance 2),
  3. the categories are compatible (evals/categories.json: same family, an alias, or an
     umbrella term such as `injection`).
Severity is NOT part of the match; it is reported separately as severity agreement.

Each issue can be found at most once and each finding finds at most one issue. Among all
ways to pair them we take a MAXIMUM matching (Kuhn's augmenting paths), so the count of hits
never depends on the order findings arrive in. A greedy "closest issue first" pairing can
under-count: a finding between two issues may take the one a second finding needed.

Every finding that is not paired gets one outcome:
  * duplicate: it matches an issue that another finding already found -> counted as a false
    positive (a second comment on the same problem is noise for the PR author),
  * optional:  it matches an optional label -> neutral, neither hit nor false positive,
  * fp:        it matches nothing.
Every issue left unpaired is a false negative (missed).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.ai.schemas import Severity
from app.services.review_poster import SEVERITY_RANK
from evals.harness.categories import CategoryTable
from evals.harness.dataset import IssueLabel

DEFAULT_TOLERANCE = 2

Outcome = Literal["tp", "duplicate", "optional", "fp"]


@dataclass(frozen=True)
class Predicted:
    """The parts of a finding the matching rule looks at."""

    agent: str
    file: str
    line: int
    end_line: int | None
    category: str
    severity: Severity
    confidence: float

    @property
    def last_line(self) -> int:
        return self.end_line or self.line


@dataclass(frozen=True)
class Judged:
    prediction: Predicted
    outcome: Outcome
    issue: int | None  # index into the required issues (tp, duplicate) or optional (optional)
    severity_ok: bool | None  # for tp: severity >= the label's min_severity


@dataclass(frozen=True)
class CaseMatch:
    judged: tuple[Judged, ...]  # one per prediction, in input order
    missed: tuple[int, ...]  # indexes of required issues nobody found

    def count(self, outcome: Outcome) -> int:
        return sum(1 for j in self.judged if j.outcome == outcome)

    @property
    def tp(self) -> int:
        return self.count("tp")

    @property
    def fp(self) -> int:
        """False positives, duplicates included."""
        return self.count("fp") + self.count("duplicate")

    @property
    def fn(self) -> int:
        return len(self.missed)


def line_gap(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    """Lines between two inclusive ranges; 0 when they overlap or touch."""
    if a_end < b_start:
        return b_start - a_end
    if b_end < a_start:
        return a_start - b_end
    return 0


def _gap(p: Predicted, issue: IssueLabel) -> int:
    return line_gap(p.line, p.last_line, issue.line, issue.last_line)


def is_match(p: Predicted, issue: IssueLabel, *, tolerance: int, categories: CategoryTable) -> bool:
    return (
        p.file == issue.file
        and _gap(p, issue) <= tolerance
        and categories.compatible(p.category, issue.category)
    )


def match_case(
    predictions: Sequence[Predicted],
    issues: Sequence[IssueLabel],
    optional: Sequence[IssueLabel] = (),
    *,
    categories: CategoryTable,
    tolerance: int = DEFAULT_TOLERANCE,
) -> CaseMatch:
    def matching(p: Predicted, labels: Sequence[IssueLabel]) -> list[int]:
        found = [
            j
            for j, issue in enumerate(labels)
            if is_match(p, issue, tolerance=tolerance, categories=categories)
        ]
        return sorted(found, key=lambda j: (_gap(p, labels[j]), labels[j].line))

    candidates = [matching(p, issues) for p in predictions]
    owner: dict[int, int] = {}  # issue index -> prediction index

    def assign(i: int, visited: set[int]) -> bool:
        for j in candidates[i]:
            if j in visited:
                continue
            visited.add(j)
            if j not in owner or assign(owner[j], visited):
                owner[j] = i
                return True
        return False

    # Most confident first, so when two findings compete for one issue on equal terms the
    # more confident one is the hit and the other the duplicate.
    order = sorted(range(len(predictions)), key=lambda i: (-predictions[i].confidence, i))
    for i in order:
        assign(i, set())

    found_by = {i: j for j, i in owner.items()}
    judged: list[Judged] = []
    for i, p in enumerate(predictions):
        if i in found_by:
            issue = issues[found_by[i]]
            ok = SEVERITY_RANK[p.severity] >= SEVERITY_RANK[issue.min_severity]
            judged.append(Judged(p, "tp", found_by[i], ok))
        elif candidates[i]:
            judged.append(Judged(p, "duplicate", candidates[i][0], None))
        elif optional_hits := matching(p, optional):
            judged.append(Judged(p, "optional", optional_hits[0], None))
        else:
            judged.append(Judged(p, "fp", None, None))
    missed = tuple(j for j in range(len(issues)) if j not in owner)
    return CaseMatch(tuple(judged), missed)
