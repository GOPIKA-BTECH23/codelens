"""Diff parsing, commentable lines, the noise filter and chunking (pure logic).

tests/fixtures/diffs/tricky.diff was produced by real `git diff -M` (not typed by hand) and
holds the awkward cases in one file: a binary file, a new file, a C-quoted non-ASCII path, a
deleted file, two hunks in one file, a rename with an edit, a pure rename, "\\ No newline at
end of file" on both sides, and a removed line whose text starts with "-- " (so it appears as
"--- schema notes", which looks like a file header).
"""

from pathlib import Path

import pytest

from app.ai.llm.base import estimate_tokens
from app.services.diff import (
    DiffParseError,
    FileDiff,
    FileStatus,
    chunk_files,
    filter_files,
    from_pr_files,
    matches_pattern,
    parse_hunks,
    parse_unified_diff,
    render_file,
)
from app.services.github_client import PullRequestFile

FIXTURES = Path(__file__).parent.parent / "fixtures" / "diffs"


@pytest.fixture(scope="module")
def tricky() -> dict[str, FileDiff]:
    files = parse_unified_diff((FIXTURES / "tricky.diff").read_text(encoding="utf-8"))
    return {f.path: f for f in files}


def test_every_file_in_the_diff_is_found(tricky: dict[str, FileDiff]) -> None:
    assert set(tricky) == {
        "assets/logo.png",
        "src/added.py",
        "src/café.py",
        "src/deleted.py",
        "src/multi.py",
        "src/new_name.py",
        "src/no_newline.py",
        "src/pure_rename_new.py",
        "src/sql.sql",
    }


def test_new_file(tricky: dict[str, FileDiff]) -> None:
    added = tricky["src/added.py"]
    assert added.status == FileStatus.ADDED
    assert added.added_lines == {1: "def hello():", 2: "    return 'hi'"}
    assert added.commentable_lines == {1, 2}


def test_deleted_file_has_nothing_to_comment_on(tricky: dict[str, FileDiff]) -> None:
    deleted = tricky["src/deleted.py"]
    assert deleted.status == FileStatus.DELETED
    assert deleted.deletions == 2
    assert deleted.commentable_lines == frozenset()


def test_multiple_hunks_map_to_new_file_line_numbers(tricky: dict[str, FileDiff]) -> None:
    multi = tricky["src/multi.py"]
    assert len(multi.hunks) == 2
    assert multi.added_lines == {3: "line 3 changed", 31: "inserted after 30"}
    assert multi.hunks[1].section == "line 27"
    # Context lines are commentable too (GitHub accepts any RIGHT-side line in a hunk) ...
    assert multi.commentable_lines == set(range(1, 7)) | set(range(28, 35))
    # ... but a line between the hunks is not, even though it exists in the file.
    assert 15 not in multi.commentable_lines


def test_rename_with_an_edit(tricky: dict[str, FileDiff]) -> None:
    renamed = tricky["src/new_name.py"]
    assert renamed.status == FileStatus.RENAMED
    assert renamed.old_path == "src/old_name.py"
    assert renamed.added_lines == {10: "value_10 = 100"}


def test_pure_rename_has_no_hunks(tricky: dict[str, FileDiff]) -> None:
    pure = tricky["src/pure_rename_new.py"]
    assert pure.status == FileStatus.RENAMED
    assert pure.old_path == "src/pure_rename_old.py"
    assert pure.hunks == ()


def test_no_newline_marker_is_not_a_line(tricky: dict[str, FileDiff]) -> None:
    f = tricky["src/no_newline.py"]
    assert [(ln.kind, ln.text) for ln in f.hunks[0].lines] == [
        (" ", "a = 1"),
        ("-", "b = 2"),
        ("+", "b = 3"),
    ]
    assert f.added_lines == {2: "b = 3"}


def test_removed_line_that_looks_like_a_file_header(tricky: dict[str, FileDiff]) -> None:
    sql = tricky["src/sql.sql"]
    assert [(ln.kind, ln.text) for ln in sql.hunks[0].lines] == [
        ("-", "-- schema notes"),
        (" ", "CREATE TABLE t (id int);"),
    ]


def test_binary_file(tricky: dict[str, FileDiff]) -> None:
    assert tricky["assets/logo.png"].is_binary
    assert tricky["assets/logo.png"].hunks == ()


def test_quoted_utf8_path_is_decoded(tricky: dict[str, FileDiff]) -> None:
    assert tricky["src/café.py"].added_lines == {1: "x = 2"}


def test_hunk_without_counts_means_one_line() -> None:
    (hunk,) = parse_hunks("@@ -1 +1 @@\n-old\n+new")
    assert (hunk.old_count, hunk.new_count) == (1, 1)


@pytest.mark.parametrize(
    "patch",
    [
        "@@ -1,2 +1,2 @@\n a\n",  # ends in the middle of the hunk
        "@@ -1 +1 @@\n+new\n+extra",  # more added lines than the header allows
        "@@ nonsense @@\n+x",
    ],
)
def test_malformed_patches_are_rejected(patch: str) -> None:
    with pytest.raises(DiffParseError):
        parse_hunks(patch)


# --- GitHub's "list pull request files" -------------------------------------------------------


def test_from_pr_files_maps_statuses_and_missing_patches() -> None:
    files = from_pr_files(
        [
            PullRequestFile(filename="a.py", status="added", patch="@@ -0,0 +1 @@\n+x = 1"),
            PullRequestFile(filename="b.py", status="removed", patch="@@ -1 +0,0 @@\n-x = 1"),
            PullRequestFile(
                filename="c.py",
                status="renamed",
                previous_filename="old_c.py",
                patch="@@ -1 +1 @@\n-x\n+y",
            ),
            PullRequestFile(filename="big.bin", status="modified", patch=None),
        ]
    )
    by_path = {f.path: f for f in files}
    assert by_path["a.py"].status == FileStatus.ADDED and by_path["a.py"].added_lines == {
        1: "x = 1"
    }
    assert by_path["b.py"].status == FileStatus.DELETED
    assert by_path["c.py"].old_path == "old_c.py"
    assert by_path["big.bin"].patch_missing


# --- noise filter -----------------------------------------------------------------------------


def _file(path: str, added: int = 1, status: FileStatus = FileStatus.MODIFIED) -> FileDiff:
    body = "\n".join(f"+line {i}" for i in range(added))
    return FileDiff(path, status, parse_hunks(f"@@ -0,0 +1,{added} @@\n{body}"))


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("frontend/package-lock.json", "package-lock.json", True),
        ("lib/vendor/x.go", "vendor/", True),
        ("vendor.py", "vendor/", False),  # a file named like the directory is not inside it
        ("static/app.min.js", "*.min.js", True),
        ("apps/web/dist/x.js", "apps/web/dist/", True),
        ("other/web/dist/x.js", "apps/web/dist/", False),
        ("src/gen/a.py", "src/gen/*.py", True),
    ],
)
def test_ignore_patterns(path: str, pattern: str, expected: bool) -> None:
    assert matches_pattern(path, pattern) is expected


def test_filter_separates_code_docs_and_noise() -> None:
    generated = FileDiff(
        "api/client.py",
        FileStatus.ADDED,
        parse_hunks("@@ -0,0 +1,2 @@\n+# Code generated by openapi-gen. DO NOT EDIT.\n+x = 1"),
    )
    result = filter_files(
        [
            _file("app/main.py"),
            _file("README.md"),
            _file("docs/guide.txt"),
            _file("poetry.lock"),
            _file("node_modules/x/index.js"),
            _file("img/logo.png"),
            _file("app/huge.py", added=50),
            _file("app/old.py", status=FileStatus.DELETED),
            _file("custom/skip_me.py"),
            FileDiff("big.dat", FileStatus.MODIFIED, patch_missing=True),
            generated,
        ],
        extra_patterns=["custom/"],
        max_changed_lines=40,
    )
    assert [f.path for f in result.code_files] == ["app/main.py"]
    assert [f.path for f in result.doc_files] == ["README.md", "docs/guide.txt"]
    reasons = {s.path: s.reason for s in result.skipped}
    assert reasons["poetry.lock"].startswith("ignored path")
    assert reasons["node_modules/x/index.js"].startswith("ignored path")
    assert reasons["custom/skip_me.py"].startswith("ignored path")
    assert reasons["img/logo.png"] == "binary"
    assert reasons["app/huge.py"] == "too large (50 changed lines)"
    assert reasons["app/old.py"] == "deleted"
    assert reasons["big.dat"].startswith("no diff from GitHub")
    assert reasons["api/client.py"] == "generated file"


# --- rendering and chunking -------------------------------------------------------------------


def test_render_numbers_right_side_lines_only(tricky: dict[str, FileDiff]) -> None:
    text = render_file(tricky["src/no_newline.py"])
    assert text.splitlines() == [
        "### File: src/no_newline.py (modified)",
        "@@ -1,2 +1,2 @@",
        "     1   a = 1",
        "       - b = 2",
        "     2 + b = 3",
    ]


def test_render_names_the_old_path_of_a_rename(tricky: dict[str, FileDiff]) -> None:
    assert render_file(tricky["src/new_name.py"]).startswith(
        "### File: src/new_name.py (renamed from src/old_name.py)"
    )


def test_small_files_share_one_chunk() -> None:
    chunks, skipped = chunk_files(
        [_file("a.py"), _file("b.py")], max_tokens=1000, estimate=estimate_tokens
    )
    assert len(chunks) == 1 and chunks[0].paths == ("a.py", "b.py")
    assert skipped == []


def test_big_files_are_split_between_hunks_and_oversized_hunks_skipped() -> None:
    hunks = []
    for start in (1, 101, 201):
        body = "\n".join(f"+code line {start + i} with some padding text" for i in range(20))
        hunks.append(f"@@ -0,0 +{start},20 @@\n{body}")
    huge_body = "\n".join(f"+x{i} = 'a long string literal that keeps going'" for i in range(200))
    hunks.append(f"@@ -0,0 +301,200 @@\n{huge_body}")
    big = FileDiff("big.py", FileStatus.MODIFIED, parse_hunks("\n".join(hunks)))

    chunks, skipped = chunk_files(
        [big, _file("small.py")], max_tokens=400, estimate=estimate_tokens
    )

    assert all(estimate_tokens(c.render()) <= 400 for c in chunks)
    reviewed_starts = sorted(h.new_start for c in chunks for f in c.files for h in f.hunks)
    assert reviewed_starts == [1, 1, 101, 201]  # big.py's three hunks + small.py's one
    assert [s.path for s in skipped] == ["big.py"]
    assert "too large" in skipped[0].reason
