"""The matching rule's edge cases (docs/evals.md, "Matching rule")."""

import pytest

from app.ai.schemas import Severity
from evals.harness.categories import load_categories
from evals.harness.dataset import IssueLabel
from evals.harness.matching import Predicted, line_gap, match_case

CATS = load_categories()
FILE = "app/users.py"


def issue(line: int, category: str = "sql-injection", end_line: int | None = None) -> IssueLabel:
    return IssueLabel(
        file=FILE, line=line, end_line=end_line, category=category, min_severity=Severity.HIGH
    )


def pred(
    line: int,
    category: str = "sql-injection",
    *,
    end_line: int | None = None,
    file: str = FILE,
    confidence: float = 0.9,
    severity: Severity = Severity.HIGH,
) -> Predicted:
    return Predicted("security", file, line, end_line, category, severity, confidence)


def outcomes(
    predictions: list[Predicted], issues: list[IssueLabel], tolerance: int = 2
) -> list[str]:
    m = match_case(predictions, issues, categories=CATS, tolerance=tolerance)
    return [j.outcome for j in m.judged]


@pytest.mark.parametrize(
    ("a", "b", "gap"),
    [((5, 5), (5, 5), 0), ((3, 7), (5, 5), 0), ((1, 2), (5, 6), 3), ((9, 9), (5, 6), 3)],
)
def test_line_gap(a: tuple[int, int], b: tuple[int, int], gap: int) -> None:
    assert line_gap(*a, *b) == gap


def test_exact_hit() -> None:
    m = match_case([pred(9)], [issue(9)], categories=CATS)
    assert (m.tp, m.fp, m.fn) == (1, 0, 0)
    assert m.judged[0].severity_ok is True


@pytest.mark.parametrize(("line", "expected"), [(11, "tp"), (7, "tp"), (12, "fp"), (6, "fp")])
def test_tolerance_boundary_is_inclusive(line: int, expected: str) -> None:
    assert outcomes([pred(line)], [issue(9)]) == [expected]


def test_strict_tolerance() -> None:
    assert outcomes([pred(10)], [issue(9)], tolerance=0) == ["fp"]


def test_ranges_are_compared_as_ranges() -> None:
    assert outcomes([pred(7, end_line=9)], [issue(8)], tolerance=0) == ["tp"]  # covers it
    assert outcomes([pred(12)], [issue(8, end_line=10)]) == ["tp"]  # 2 after the range end


def test_wrong_file_is_a_false_positive_and_a_miss() -> None:
    m = match_case([pred(9, file="app/other.py")], [issue(9)], categories=CATS)
    assert (m.tp, m.fp, m.fn) == (0, 1, 1)


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("sqli", "tp"),  # alias
        ("SQL_Injection", "tp"),  # normalised like the Finding schema does
        ("injection", "tp"),  # umbrella term
        ("command-injection", "fp"),  # a different family
        ("xss", "fp"),
        ("weird-thing", "fp"),  # unknown to the table
    ],
)
def test_category_compatibility(category: str, expected: str) -> None:
    assert outcomes([pred(9, category)], [issue(9)]) == [expected]


def test_second_finding_on_the_same_issue_is_a_duplicate_false_positive() -> None:
    m = match_case(
        [pred(10, confidence=0.7), pred(9, confidence=0.95)], [issue(9)], categories=CATS
    )
    assert [j.outcome for j in m.judged] == ["duplicate", "tp"]  # the more confident one wins
    assert (m.tp, m.fp, m.fn) == (1, 1, 0)


def test_optional_label_is_neutral_but_required_wins() -> None:
    optional = [issue(11, "prompt-injection")]
    m = match_case(
        [pred(11, "prompt-injection")], [issue(14, "command-injection")], optional, categories=CATS
    )
    assert [j.outcome for j in m.judged] == ["optional"]
    assert (m.tp, m.fp, m.fn) == (0, 0, 1)

    both = [issue(9)]
    m = match_case([pred(9)], both, [issue(9)], categories=CATS)
    assert [j.outcome for j in m.judged] == ["tp"]


def test_maximum_matching_beats_greedy() -> None:
    # Finding A (line 10) is 1 line from both issues; finding B (line 8) only reaches issue 9.
    # Greedy-by-confidence-then-closest gives A the issue at 9 and leaves B with nothing:
    # 1 hit. The maximum matching pairs A with 11 and B with 9: 2 hits.
    issues = [issue(9), issue(11)]
    m = match_case([pred(10, confidence=0.99), pred(7, confidence=0.5)], issues, categories=CATS)
    assert (m.tp, m.fp, m.fn) == (2, 0, 0)
    assert {j.issue for j in m.judged} == {0, 1}


def test_lower_severity_still_matches_but_disagrees() -> None:
    m = match_case([pred(9, severity=Severity.MEDIUM)], [issue(9)], categories=CATS)
    assert m.tp == 1 and m.judged[0].severity_ok is False


def test_empty_sides() -> None:
    m = match_case([], [issue(9), issue(14)], categories=CATS)
    assert (m.tp, m.fp, m.fn) == (0, 0, 2)
    m = match_case([pred(3), pred(4)], [], categories=CATS)
    assert (m.tp, m.fp, m.fn) == (0, 2, 0)
