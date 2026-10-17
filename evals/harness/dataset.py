"""The eval dataset: loading and validation.

Layout (evals/dataset/README.md has the full description):
    seeded_prs/<case>.diff            a real `git diff`
    seeded_prs/<case>.expected.json   labels (CaseLabels below, schema_version 2)
    splits.json                       which case is in dev and which in test
    candidates/                       model-drafted cases waiting for human review

Only `seeded_prs/` is ever scored. `validate_dataset` checks everything a wrong label could
silently break: the diff parses, every labelled line is an ADDED line of that file (the
pipeline only reviews changes), `clean` agrees with the labels, `has_code_to_review` agrees
with the production noise filter, categories exist in categories.json, and every case is in
exactly one split.
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.ai.schemas import Severity
from app.services.diff import DiffParseError, FileDiff, filter_files, parse_unified_diff
from evals.harness.categories import CategoryTable, load_categories

DATASET_DIR = Path(__file__).resolve().parents[1] / "dataset"
SEEDED_DIR = DATASET_DIR / "seeded_prs"
CANDIDATES_DIR = DATASET_DIR / "candidates"
SPLITS_FILE = DATASET_DIR / "splits.json"
SCHEMA_VERSION = 2
Split = Literal["dev", "test"]


class IssueLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1)
    line: int = Field(ge=1, description="The anchor line: an added line where the issue is.")
    end_line: int | None = Field(
        default=None, ge=1, description="Last line if the issue spans several lines."
    )
    category: str = Field(description="A family name from evals/categories.json.")
    min_severity: Severity = Field(description="The lowest severity a good review would give.")
    note: str | None = Field(default=None, description="Why this is (or might be) an issue.")

    @model_validator(mode="after")
    def _range(self) -> "IssueLabel":
        if self.end_line is not None and self.end_line < self.line:
            raise ValueError("end_line must not be before line")
        return self

    @property
    def last_line(self) -> int:
        return self.end_line or self.line


class PRMeta(BaseModel):
    """PR metadata. Recorded for realism and future agents; the security agent reads only
    the diff today."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    body: str = ""


class CaseLabels(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2]
    case: str
    description: str
    source: str = Field(description="Where the diff comes from, e.g. 'synthetic'.")
    label_status: Literal["accepted", "draft"] = Field(
        description="draft = model-drafted, not yet reviewed by a human (candidates/ only)."
    )
    labelled_by: str = Field(description="Who wrote and who reviewed the labels.")
    pr: PRMeta
    clean: bool = Field(description="True when a good review reports nothing.")
    has_code_to_review: bool = Field(description="False when the noise filter drops it all.")
    expected_findings: list[IssueLabel]
    optional_findings: list[IssueLabel] = Field(default_factory=list)


@dataclass(frozen=True)
class Case:
    name: str
    diff: str
    files: list[FileDiff]
    labels: CaseLabels
    split: Split | None  # None outside seeded_prs (candidates have no split yet)

    @property
    def issues(self) -> list[IssueLabel]:
        return self.labels.expected_findings

    @property
    def optional(self) -> list[IssueLabel]:
        return self.labels.optional_findings


@dataclass(frozen=True)
class Dataset:
    name: str
    version: str  # content hash of every case file and splits.json
    cases: tuple[Case, ...]
    splits_status: str  # "provisional" until the split is approved

    def select(self, split: Split | Literal["all"], names: Iterable[str] = ()) -> list[Case]:
        wanted = set(names)
        return [
            c
            for c in self.cases
            if (split == "all" or c.split == split) and (not wanted or c.name in wanted)
        ]


class DatasetError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _read(path: Path) -> str:
    # .gitattributes forces LF, but a Windows editor could still save CRLF locally.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def dataset_version(directory: Path, splits_file: Path | None) -> str:
    digest = hashlib.sha256()
    paths = sorted(p for p in directory.iterdir() if p.suffix in (".diff", ".json"))
    if splits_file is not None:
        paths.append(splits_file)
    for path in paths:
        digest.update(path.name.encode() + b"\0" + _read(path).encode() + b"\0")
    return digest.hexdigest()[:12]


def _load_splits(splits_file: Path, problems: list[str]) -> tuple[dict[str, Split], str]:
    data = json.loads(_read(splits_file))
    assignment: dict[str, Split] = {}
    splits: tuple[Split, ...] = ("dev", "test")
    for split in splits:
        for name in data.get(split, []):
            if name in assignment:
                problems.append(f"splits.json: {name} is in more than one split")
            assignment[name] = split
    return assignment, str(data.get("status", "unknown"))


def _check_issue(
    case: str, kind: str, issue: IssueLabel, by_path: dict[str, FileDiff], categories: CategoryTable
) -> list[str]:
    where = f"{case}: {kind} {issue.file}:{issue.line}"
    if issue.category not in categories.families:
        return [f"{where}: category '{issue.category}' is not a family in categories.json"]
    file = by_path.get(issue.file)
    if file is None:
        return [f"{where}: file is not in the diff"]
    if issue.line not in file.added_lines:
        return [f"{where}: line {issue.line} is not an added line"]
    hunk = file.hunk_of(issue.line)
    assert hunk is not None  # an added line is always inside a hunk
    if any(n not in hunk.right_lines for n in range(issue.line, issue.last_line + 1)):
        return [f"{where}: range {issue.line}-{issue.last_line} leaves the hunk"]
    return []


def _check_case(
    name: str, directory: Path, categories: CategoryTable, allow_drafts: bool
) -> tuple[Case | None, list[str]]:
    try:
        labels = CaseLabels.model_validate_json(_read(directory / f"{name}.expected.json"))
    except ValidationError as exc:
        locs = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        return None, [f"{name}.expected.json: {loc}" for loc in locs]
    diff = _read(directory / f"{name}.diff")
    try:
        files = parse_unified_diff(diff)
    except DiffParseError as exc:
        return None, [f"{name}.diff does not parse: {exc}"]

    problems: list[str] = []
    if not files:
        problems.append(f"{name}.diff contains no files")
    if labels.case != name:
        problems.append(f"{name}: 'case' says {labels.case!r}")
    if labels.label_status == "draft" and not allow_drafts:
        problems.append(f"{name}: draft labels are only allowed in candidates/")
    count = len(labels.expected_findings)
    if labels.clean != (count == 0):
        problems.append(f"{name}: clean={labels.clean} but it has {count} issue(s)")
    has_code = bool(filter_files(files).code_files)
    if labels.has_code_to_review != has_code:
        problems.append(
            f"{name}: has_code_to_review={labels.has_code_to_review}, the noise filter says "
            f"{has_code}"
        )
    by_path = {f.path: f for f in files}
    seen: set[tuple[str, int, str]] = set()
    for kind, issues in (
        ("issue", labels.expected_findings),
        ("optional", labels.optional_findings),
    ):
        for issue in issues:
            problems += _check_issue(name, kind, issue, by_path, categories)
            key = (issue.file, issue.line, issue.category)
            if key in seen:
                problems.append(f"{name}: duplicate label {issue.file}:{issue.line}")
            seen.add(key)
    return Case(name, diff, files, labels, None), problems


def validate_dataset(
    directory: Path = SEEDED_DIR,
    *,
    splits_file: Path | None = SPLITS_FILE,
    allow_drafts: bool = False,
    allow_empty: bool = False,
    categories: CategoryTable | None = None,
) -> tuple[list[Case], list[str]]:
    """Every problem found (empty = valid) and the cases that loaded.

    `allow_empty` is for candidates/, which is empty once every draft has been reviewed and
    moved. The scored dataset must never be empty: a missing seeded_prs/ would otherwise
    "pass" with no cases and every metric undefined.
    """
    categories = categories or load_categories()
    problems: list[str] = []
    diffs = {p.name.removesuffix(".diff") for p in directory.glob("*.diff")}
    labels = {p.name.removesuffix(".expected.json") for p in directory.glob("*.expected.json")}
    problems += [f"{n}: .diff without .expected.json" for n in sorted(diffs - labels)]
    problems += [f"{n}: .expected.json without .diff" for n in sorted(labels - diffs)]
    allowed = {f"{n}.diff" for n in diffs} | {f"{n}.expected.json" for n in labels}
    allowed |= {"README.md", ".gitkeep"}
    problems += [f"unexpected file {p.name}" for p in directory.iterdir() if p.name not in allowed]

    assignment: dict[str, Split] = {}
    if splits_file is not None:
        assignment, _ = _load_splits(splits_file, problems)
        problems += [f"splits.json: unknown case {n}" for n in sorted(set(assignment) - diffs)]
    cases: list[Case] = []
    for name in sorted(diffs & labels):
        case, case_problems = _check_case(name, directory, categories, allow_drafts)
        problems += case_problems
        if splits_file is not None and name not in assignment:
            problems.append(f"{name}: not in any split (splits.json)")
        if case is not None:
            cases.append(Case(case.name, case.diff, case.files, case.labels, assignment.get(name)))
    if not cases and not problems and not allow_empty:
        problems.append(f"no cases in {directory}")
    return cases, problems


def load_dataset(directory: Path = SEEDED_DIR, splits_file: Path = SPLITS_FILE) -> Dataset:
    """The validated, scored dataset. Raises DatasetError listing every problem."""
    cases, problems = validate_dataset(directory, splits_file=splits_file)
    if problems:
        raise DatasetError(problems)
    _, status = _load_splits(splits_file, [])
    return Dataset(directory.name, dataset_version(directory, splits_file), tuple(cases), status)


def describe(cases: Iterable[Case]) -> str:
    """The sample-size line every report starts with."""
    cases = list(cases)
    clean = [c for c in cases if c.labels.clean]
    clean_code = [c for c in clean if c.labels.has_code_to_review]
    issues = sum(len(c.issues) for c in cases)
    optional = sum(len(c.optional) for c in cases)
    return (
        f"{len(cases)} cases ({len(cases) - len(clean)} with issues, {len(clean)} clean "
        f"[{len(clean_code)} with code]), {issues} required issues, {optional} optional"
    )
