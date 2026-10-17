"""The eval dataset validator: the committed dataset is valid, and each check catches the
mistake it exists for."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from evals.harness.dataset import (
    CANDIDATES_DIR,
    SEEDED_DIR,
    dataset_version,
    describe,
    load_dataset,
    validate_dataset,
)
from evals.validate_dataset import main as validate_main

ORIGINAL_SEVEN = {
    "clean_change",
    "docs_only",
    "hardcoded_secret",
    "path_traversal",
    "prompt_injection",
    "sql_injection",
    "unsafe_deserialization",
}


def test_committed_dataset_is_valid() -> None:
    dataset = load_dataset()
    assert len(dataset.cases) == len(list(SEEDED_DIR.glob("*.diff")))
    assert {c.split for c in dataset.cases} == {"dev", "test"}
    assert all(c.labels.label_status == "accepted" for c in dataset.cases)
    assert validate_main([]) == 0


def test_sample_size_line() -> None:
    original = [c for c in load_dataset().cases if c.name in ORIGINAL_SEVEN]
    assert describe(original) == (
        "7 cases (5 with issues, 2 clean [1 with code]), 8 required issues, 1 optional"
    )


def test_candidates_are_valid_drafts_and_never_scored() -> None:
    """Whatever is in candidates/ (possibly nothing) is valid, still a draft, and not in the
    scored dataset."""
    cases, problems = validate_dataset(
        CANDIDATES_DIR, splits_file=None, allow_drafts=True, allow_empty=True
    )
    assert problems == []
    assert all(c.labels.label_status == "draft" for c in cases)
    assert validate_main(["--candidates"]) == 0
    scored = {c.name for c in load_dataset().cases}
    assert scored.isdisjoint(c.name for c in cases)
    assert (CANDIDATES_DIR / ".gitkeep").is_file()  # keeps the folder tracked when empty


def test_empty_candidates_dir_is_valid_but_an_empty_scored_dir_is_not(tmp_path: Path) -> None:
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    assert validate_dataset(tmp_path, splits_file=None, allow_drafts=True, allow_empty=True) == (
        [],
        [],
    )
    _, problems = validate_dataset(tmp_path, splits_file=None)
    assert problems == [f"no cases in {tmp_path}"]


def test_malformed_draft_is_still_rejected(tmp_path: Path) -> None:
    def broken_draft(labels: dict[str, Any]) -> None:
        labels["label_status"] = "draft"
        labels["expected_findings"][0]["line"] = 4  # a context line, not an added one

    directory = copy_case(tmp_path, mutate=broken_draft)
    _, problems = validate_dataset(directory, splits_file=None, allow_drafts=True, allow_empty=True)
    assert problems == ["sql_injection: issue app/users.py:4: line 4 is not an added line"]


def test_select_by_split_and_name() -> None:
    dataset = load_dataset()
    dev = dataset.select("dev")
    assert dev and all(c.split == "dev" for c in dev)
    assert [c.name for c in dataset.select("all", ["sql_injection"])] == ["sql_injection"]


# --- one broken copy per check ------------------------------------------------------------------

Mutate = Callable[[dict[str, Any]], None]


def copy_case(tmp_path: Path, name: str = "sql_injection", mutate: Mutate | None = None) -> Path:
    shutil.copy(SEEDED_DIR / f"{name}.diff", tmp_path / f"{name}.diff")
    labels = json.loads((SEEDED_DIR / f"{name}.expected.json").read_text(encoding="utf-8"))
    if mutate is not None:
        mutate(labels)
    (tmp_path / f"{name}.expected.json").write_text(json.dumps(labels), encoding="utf-8")
    return tmp_path


def problems_for(tmp_path: Path, mutate: Mutate, name: str = "sql_injection") -> list[str]:
    _, problems = validate_dataset(copy_case(tmp_path, name, mutate), splits_file=None)
    return problems


def set_issue(**changes: Any) -> Mutate:
    def mutate(labels: dict[str, Any]) -> None:
        labels["expected_findings"][0].update(changes)

    return mutate


def test_unchanged_copy_is_valid(tmp_path: Path) -> None:
    assert problems_for(tmp_path, lambda labels: None) == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (set_issue(line=4), "line 4 is not an added line"),  # a context line
        (set_issue(line=99), "line 99 is not an added line"),
        (set_issue(file="app/other.py"), "file is not in the diff"),
        (set_issue(category="sqli"), "is not a family in categories.json"),  # alias, not family
        (set_issue(end_line=40), "leaves the hunk"),
        (set_issue(min_severity="catastrophic"), "min_severity"),
        (lambda labels: labels.update(clean=True), "clean=True but it has 2 issue(s)"),
        (lambda labels: labels.update(has_code_to_review=False), "the noise filter says True"),
        (lambda labels: labels.update(case="other"), "'case' says 'other'"),
        (lambda labels: labels.update(label_status="draft"), "draft labels are only allowed"),
        (lambda labels: labels.update(surprise=1), "surprise"),
        (
            lambda labels: labels["expected_findings"].append(labels["expected_findings"][0]),
            "duplicate label",
        ),
    ],
)
def test_each_check_reports_its_problem(tmp_path: Path, mutate: Mutate, expected: str) -> None:
    problems = problems_for(tmp_path, mutate)
    assert any(expected in p for p in problems), problems


def test_docs_only_case_must_say_it_has_no_code(tmp_path: Path) -> None:
    problems = problems_for(
        tmp_path, lambda labels: labels.update(has_code_to_review=True), "docs_only"
    )
    assert any("the noise filter says False" in p for p in problems)


def test_drafts_are_allowed_where_asked(tmp_path: Path) -> None:
    directory = copy_case(tmp_path, mutate=lambda labels: labels.update(label_status="draft"))
    assert validate_dataset(directory, splits_file=None, allow_drafts=True)[1] == []


def test_missing_pair_and_stray_files(tmp_path: Path) -> None:
    copy_case(tmp_path)
    (tmp_path / "orphan.diff").write_text("", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")
    _, problems = validate_dataset(tmp_path, splits_file=None)
    assert "orphan: .diff without .expected.json" in problems
    assert "unexpected file notes.txt" in problems


def test_split_must_cover_every_case_exactly_once(tmp_path: Path) -> None:
    copy_case(tmp_path)
    copy_case(tmp_path, "clean_change")
    splits = tmp_path / "splits.json"
    splits.write_text(
        json.dumps({"dev": ["sql_injection", "ghost"], "test": ["sql_injection"]}),
        encoding="utf-8",
    )
    _, problems = validate_dataset(tmp_path, splits_file=splits)
    assert "splits.json: sql_injection is in more than one split" in problems
    assert "splits.json: unknown case ghost" in problems
    assert "clean_change: not in any split (splits.json)" in problems


def test_version_ignores_line_endings_but_not_content(tmp_path: Path) -> None:
    copy_case(tmp_path)
    before = dataset_version(tmp_path, None)
    diff = tmp_path / "sql_injection.diff"
    diff.write_bytes(diff.read_bytes().replace(b"\n", b"\r\n"))
    assert dataset_version(tmp_path, None) == before
    diff.write_bytes(diff.read_bytes() + b"+extra\n")
    assert dataset_version(tmp_path, None) != before
