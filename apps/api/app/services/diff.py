"""Unified diffs: parse them, decide which lines can carry a review comment, drop noise, and
split big PRs into chunks the model can handle.

Background (verified against GitHub's REST docs on 2026-10-16, see github_client.py): a
review comment is anchored with `path`, `line` and `side`. With side=RIGHT, `line` is a line
number in the NEW file and must fall inside one of the diff's hunks (an added or unchanged
line shown in the diff). Anything else makes GitHub reject the WHOLE review with 422. So we
compute, per file, the set of commentable RIGHT-side lines, and review_poster drops any
finding that does not land on one.

Two inputs, one result:
  * `parse_unified_diff(text)`: a full `git diff` (fixtures, evals, the .diff media type).
  * `from_pr_files(files)`: GitHub's "list pull request files" response, where each file has
    its own `patch` (just the hunks). The worker uses this one (decision 15): it is paginated
    and works for diffs too large for the .diff endpoint.

Hunk parsing is driven by the line COUNTS in the `@@ -a,b +c,d @@` header, not by guessing
from the line's first character. That matters: a removed line whose text is `-- note` shows
up as `--- note`, which a naive parser would take for the next file's header.
"""

import fnmatch
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Literal

from app.services.github_client import PullRequestFile

LineKind = Literal["+", "-", " "]

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
_NO_NEWLINE = "\\ No newline at end of file"


class FileStatus(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"


@dataclass(frozen=True)
class DiffLine:
    kind: LineKind
    text: str
    old_line: int | None  # None for added lines
    new_line: int | None  # None for removed lines


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section: str  # text after the second @@ (often the enclosing function)
    lines: tuple[DiffLine, ...]

    @property
    def header(self) -> str:
        tail = f" {self.section}" if self.section else ""
        return f"@@ -{self.old_start},{self.old_count} +{self.new_start},{self.new_count} @@{tail}"

    @property
    def right_lines(self) -> frozenset[int]:
        """New-file lines shown in this hunk: the lines GitHub accepts a side=RIGHT comment on."""
        return frozenset(ln.new_line for ln in self.lines if ln.new_line is not None)


@dataclass(frozen=True)
class FileDiff:
    path: str  # the new path (the old one for a deleted file)
    status: FileStatus
    hunks: tuple[Hunk, ...] = ()
    old_path: str | None = None  # set for renames and copies
    is_binary: bool = False
    patch_missing: bool = False  # GitHub sent no patch (binary or too large)

    @property
    def additions(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.kind == "+")

    @property
    def deletions(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.kind == "-")

    @property
    def commentable_lines(self) -> frozenset[int]:
        return frozenset().union(*(h.right_lines for h in self.hunks))

    @property
    def added_lines(self) -> dict[int, str]:
        return {
            ln.new_line: ln.text
            for h in self.hunks
            for ln in h.lines
            if ln.kind == "+" and ln.new_line is not None
        }

    def right_text(self) -> dict[int, str]:
        """new line number -> text, for every RIGHT-side line shown in the diff."""
        return {
            ln.new_line: ln.text for h in self.hunks for ln in h.lines if ln.new_line is not None
        }

    def hunk_of(self, line: int) -> Hunk | None:
        return next((h for h in self.hunks if line in h.right_lines), None)


class DiffParseError(ValueError):
    """The text is not a diff we understand (the message says where)."""


# --- parsing -----------------------------------------------------------------------------------


def parse_hunks(patch: str) -> tuple[Hunk, ...]:
    """Parse the hunks of ONE file (what GitHub puts in `patch`)."""
    lines = patch.splitlines()
    hunks, rest = _read_hunks(lines, 0)
    if rest < len(lines):
        raise DiffParseError(f"unexpected text after the hunks at line {rest + 1}")
    return hunks


def _read_hunks(lines: Sequence[str], i: int) -> tuple[tuple[Hunk, ...], int]:
    """Read consecutive hunks starting at lines[i]; return them and the index after them."""
    hunks: list[Hunk] = []
    while i < len(lines) and lines[i].startswith("@@"):
        match = _HUNK_HEADER.match(lines[i])
        if match is None:
            raise DiffParseError(f"bad hunk header at line {i + 1}")
        old_start, new_start = int(match[1]), int(match[3])
        old_count = int(match[2]) if match[2] is not None else 1
        new_count = int(match[4]) if match[4] is not None else 1
        i += 1
        old_left, new_left = old_count, new_count
        old_no, new_no = old_start, new_start
        body: list[DiffLine] = []
        while old_left > 0 or new_left > 0:
            if i >= len(lines):
                raise DiffParseError("diff ended in the middle of a hunk")
            raw = lines[i]
            i += 1
            if raw == _NO_NEWLINE:
                continue
            marker, text = (raw[0], raw[1:]) if raw else (" ", "")  # some tools strip " "
            if marker == "+" and new_left > 0:
                body.append(DiffLine("+", text, None, new_no))
                new_no += 1
                new_left -= 1
            elif marker == "-" and old_left > 0:
                body.append(DiffLine("-", text, old_no, None))
                old_no += 1
                old_left -= 1
            elif marker == " " and old_left > 0 and new_left > 0:
                body.append(DiffLine(" ", text, old_no, new_no))
                old_no += 1
                new_no += 1
                old_left -= 1
                new_left -= 1
            else:
                raise DiffParseError(f"line {i} does not fit the hunk's line counts")
        while i < len(lines) and lines[i] == _NO_NEWLINE:
            i += 1
        hunks.append(
            Hunk(old_start, old_count, new_start, new_count, match[5].strip(), tuple(body))
        )
    return tuple(hunks), i


def parse_unified_diff(text: str) -> list[FileDiff]:
    """Parse a full `git diff` (several files, with `diff --git` headers)."""
    lines = text.splitlines()
    files: list[FileDiff] = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith("diff --git "):
            i += 1  # preamble (e.g. a commit message) or blank lines between files
            continue
        file, i = _read_file(lines, i)
        files.append(file)
    return files


def _read_file(lines: Sequence[str], i: int) -> tuple[FileDiff, int]:
    header_old, header_new = _split_git_header(lines[i])
    i += 1
    status = FileStatus.MODIFIED
    old_path: str | None = header_old
    new_path: str | None = header_new
    is_binary = False
    while i < len(lines) and not lines[i].startswith(("diff --git ", "@@")):
        line = lines[i]
        if line.startswith("new file mode"):
            status = FileStatus.ADDED
        elif line.startswith("deleted file mode"):
            status = FileStatus.DELETED
        elif line.startswith("rename from "):
            status, old_path = FileStatus.RENAMED, _unquote(line[len("rename from ") :])
        elif line.startswith("rename to "):
            new_path = _unquote(line[len("rename to ") :])
        elif line.startswith("copy from "):
            status, old_path = FileStatus.COPIED, _unquote(line[len("copy from ") :])
        elif line.startswith("copy to "):
            new_path = _unquote(line[len("copy to ") :])
        elif line.startswith("--- "):
            old_path = _strip_prefix(_unquote(line[4:]), "a/")
        elif line.startswith("+++ "):
            new_path = _strip_prefix(_unquote(line[4:]), "b/")
        elif line.startswith(("Binary files ", "GIT binary patch")):
            is_binary = True
        i += 1
    hunks, i = _read_hunks(lines, i)
    if new_path is None:  # "+++ /dev/null": deleted file
        status = FileStatus.DELETED
    if old_path is None and status == FileStatus.MODIFIED:  # "--- /dev/null"
        status = FileStatus.ADDED
    path = new_path if new_path is not None else old_path
    if path is None:
        raise DiffParseError(f"no file path near line {i}")
    return (
        FileDiff(
            path=path,
            status=status,
            hunks=hunks,
            old_path=old_path if status in (FileStatus.RENAMED, FileStatus.COPIED) else None,
            is_binary=is_binary,
        ),
        i,
    )


def _split_git_header(line: str) -> tuple[str | None, str | None]:
    """`diff --git a/x b/y` -> ("x", "y"). Only a fallback: the ---/+++ and rename lines are
    unambiguous and win when present (a header path may itself contain " b/")."""
    rest = line[len("diff --git ") :]
    if rest.startswith('"'):
        end = _closing_quote(rest)
        first, second = rest[: end + 1], rest[end + 2 :]
        return _strip_prefix(_unquote(first), "a/"), _strip_prefix(_unquote(second), "b/")
    # Same path on both sides (the common case): "a/P b/P".
    half = (len(rest) - 1) // 2
    if rest[half] == " " and rest[:half].removeprefix("a/") == rest[half + 1 :].removeprefix("b/"):
        path = rest[:half].removeprefix("a/")
        return path, path
    a, _, b = rest.partition(" b/")
    return a.removeprefix("a/"), b or None


def _closing_quote(text: str) -> int:
    i = 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i
        i += 1
    raise DiffParseError("unterminated quoted path")


def _unquote(path: str) -> str | None:
    """Git C-quotes paths with special characters ("a/caf\\303\\251.py"). /dev/null -> None."""
    path = path.split("\t", 1)[0]  # `diff -u` style headers append a timestamp after a tab
    if path == "/dev/null":
        return None
    if not (path.startswith('"') and path.endswith('"')):
        return path
    inner = path[1:-1]
    try:
        # The octal escapes are the path's UTF-8 bytes: unescape to one char per byte, then
        # reinterpret those bytes as UTF-8.
        return inner.encode("latin-1").decode("unicode_escape").encode("latin-1").decode("utf-8")
    except UnicodeError:
        return inner


def _strip_prefix(path: str | None, prefix: str) -> str | None:
    return path[len(prefix) :] if path is not None and path.startswith(prefix) else path


_GITHUB_STATUS = {
    "added": FileStatus.ADDED,
    "removed": FileStatus.DELETED,
    "modified": FileStatus.MODIFIED,
    "changed": FileStatus.MODIFIED,  # mode change
    "unchanged": FileStatus.MODIFIED,
    "renamed": FileStatus.RENAMED,
    "copied": FileStatus.COPIED,
}


def from_pr_files(files: Iterable[PullRequestFile]) -> list[FileDiff]:
    """Build FileDiffs from GitHub's "list pull request files" response."""
    result = []
    for f in files:
        status = _GITHUB_STATUS.get(f.status, FileStatus.MODIFIED)
        old_path = (
            f.previous_filename if status in (FileStatus.RENAMED, FileStatus.COPIED) else None
        )
        if f.patch is None:
            # Binary or too large: GitHub sends no patch. A pure rename with no content change
            # also has none, but then there is nothing to review either.
            result.append(FileDiff(f.filename, status, old_path=old_path, patch_missing=True))
            continue
        result.append(FileDiff(f.filename, status, parse_hunks(f.patch), old_path=old_path))
    return result


# --- noise filter ------------------------------------------------------------------------------

# Built-in "never review" patterns (REVIEW_IGNORE_PATTERNS adds to these). Forms:
#   "dir/"       a directory anywhere in the path
#   "a/b/*.py"   a glob over the whole path (contains "/")
#   "*.min.js"   a glob over the file name
DEFAULT_IGNORE_PATTERNS: tuple[str, ...] = (
    # lockfiles
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "poetry.lock",
    "uv.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "composer.lock",
    "go.sum",
    "packages.lock.json",
    # minified, bundled and generated code
    "*.min.js",
    "*.min.css",
    "*.map",
    "*_pb2.py",
    "*_pb2_grpc.py",
    "*.pb.go",
    "*.generated.*",
    "*.snap",
    # vendored and build output
    "vendor/",
    "node_modules/",
    "third_party/",
    "dist/",
    "build/",
    "__generated__/",
    ".yarn/",
)

BINARY_EXTENSIONS = frozenset(
    [
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".bmp",
        ".tiff",
        ".pdf",
        ".zip",
        ".gz",
        ".tgz",
        ".bz2",
        ".xz",
        ".7z",
        ".tar",
        ".jar",
        ".war",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".mp3",
        ".mp4",
        ".mov",
        ".avi",
        ".wav",
        ".so",
        ".dll",
        ".dylib",
        ".exe",
        ".bin",
        ".class",
        ".pyc",
        ".o",
        ".a",
        ".wasm",
        ".sqlite",
        ".db",
    ]
)
DOC_EXTENSIONS = frozenset([".md", ".mdx", ".rst", ".txt", ".adoc"])
DOC_FILENAMES = frozenset({"LICENSE", "LICENCE", "NOTICE", "AUTHORS", "CODEOWNERS"})
_GENERATED_MARKERS = ("@generated", "DO NOT EDIT", "Code generated by")


@dataclass(frozen=True)
class SkippedFile:
    path: str
    reason: str


@dataclass(frozen=True)
class FilterResult:
    code_files: list[FileDiff]  # what the security agent reviews
    doc_files: list[FileDiff]  # changed, but documentation: not security-reviewed
    skipped: list[SkippedFile]  # noise, with the reason (named in the summary)


def matches_pattern(path: str, pattern: str) -> bool:
    if pattern.endswith("/"):
        directory = pattern.rstrip("/")
        if "/" in directory:  # "apps/web/dist/": a specific directory
            return path.startswith(pattern) or f"/{pattern}" in f"/{path}"
        return directory in path.split("/")[:-1]
    if "/" in pattern:
        return fnmatch.fnmatchcase(path, pattern)
    return fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], pattern)


def is_doc_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    suffix = f".{name.rsplit('.', 1)[-1].lower()}" if "." in name else ""
    return suffix in DOC_EXTENSIONS or name in DOC_FILENAMES or path.startswith("docs/")


def _looks_generated(file: FileDiff) -> bool:
    first_lines = [ln.text for h in file.hunks[:1] for ln in h.lines[:5]]
    return any(marker in text for text in first_lines for marker in _GENERATED_MARKERS)


def filter_files(
    files: Iterable[FileDiff],
    *,
    extra_patterns: Sequence[str] = (),
    max_changed_lines: int = 1000,
) -> FilterResult:
    """Split the PR's files into code to review, docs, and skipped noise (with reasons)."""
    patterns = (*DEFAULT_IGNORE_PATTERNS, *extra_patterns)
    code: list[FileDiff] = []
    docs: list[FileDiff] = []
    skipped: list[SkippedFile] = []
    for file in files:
        suffix = f".{file.path.rsplit('.', 1)[-1].lower()}" if "." in file.path else ""
        reason: str | None = None
        if file.status == FileStatus.DELETED:
            reason = "deleted"
        elif file.is_binary or suffix in BINARY_EXTENSIONS:
            reason = "binary"
        elif file.patch_missing:
            reason = "no diff from GitHub (binary or too large)"
        elif any(matches_pattern(file.path, p) for p in patterns):
            reason = "ignored path (lockfile, vendored, minified or generated)"
        elif not file.added_lines:
            reason = "no added lines"
        elif file.additions + file.deletions > max_changed_lines:
            reason = f"too large ({file.additions + file.deletions} changed lines)"
        elif _looks_generated(file):
            reason = "generated file"
        if reason is not None:
            skipped.append(SkippedFile(file.path, reason))
        elif is_doc_file(file.path):
            docs.append(file)
        else:
            code.append(file)
    return FilterResult(code, docs, skipped)


# --- rendering and chunking --------------------------------------------------------------------


def render_file(file: FileDiff) -> str:
    """The text the model sees for one file. The left column is the NEW-file line number:
    the only numbers a finding may cite. Removed lines get no number."""
    origin = f"{file.status.value} from {file.old_path}" if file.old_path else file.status.value
    out = [f"### File: {file.path} ({origin})"]
    for hunk in file.hunks:
        out.append(hunk.header)
        for ln in hunk.lines:
            number = f"{ln.new_line:>6}" if ln.new_line is not None else " " * 6
            out.append(f"{number} {ln.kind} {ln.text}")
    return "\n".join(out)


@dataclass(frozen=True)
class DiffChunk:
    files: tuple[FileDiff, ...]  # a file may appear with only some of its hunks
    paths: tuple[str, ...] = field(default=())

    def render(self) -> str:
        return "\n\n".join(render_file(f) for f in self.files)


def chunk_files(
    files: Sequence[FileDiff],
    *,
    max_tokens: int,
    estimate: Callable[[str], int],
) -> tuple[list[DiffChunk], list[SkippedFile]]:
    """Pack files into chunks of at most `max_tokens` (estimated). A file too big for one
    chunk is split between hunks; a single hunk too big on its own is skipped (and named)."""
    pieces: list[FileDiff] = []
    skipped: list[SkippedFile] = []
    for file in files:
        if estimate(render_file(file)) <= max_tokens:
            pieces.append(file)
            continue
        current: list[Hunk] = []
        for hunk in file.hunks:
            if estimate(render_file(replace(file, hunks=(hunk,)))) > max_tokens:
                skipped.append(SkippedFile(file.path, f"change block {hunk.header} too large"))
                continue
            if (
                current
                and estimate(render_file(replace(file, hunks=(*current, hunk)))) > max_tokens
            ):
                pieces.append(replace(file, hunks=tuple(current)))
                current = []
            current.append(hunk)
        if current:
            pieces.append(replace(file, hunks=tuple(current)))

    chunks: list[DiffChunk] = []
    batch: list[FileDiff] = []
    for piece in pieces:
        candidate = [*batch, piece]
        if batch and estimate(DiffChunk(tuple(candidate)).render()) > max_tokens:
            chunks.append(_chunk(batch))
            candidate = [piece]
        batch = candidate
    if batch:
        chunks.append(_chunk(batch))
    return chunks, skipped


def _chunk(files: Sequence[FileDiff]) -> DiffChunk:
    return DiffChunk(tuple(files), tuple(dict.fromkeys(f.path for f in files)))
