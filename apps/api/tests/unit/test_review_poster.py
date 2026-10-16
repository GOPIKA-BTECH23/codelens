"""review_poster: findings are checked against the diff before anything reaches GitHub."""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx

from app.ai.schemas import Finding
from app.services.diff import FileDiff, SkippedFile
from app.services.github_client import GitHubClient
from app.services.review_poster import (
    DropCounts,
    ReviewSummary,
    build_review,
    defuse,
    post_review,
    render_summary,
    run_marker,
    select_findings,
    validate_findings,
)
from tests.seeded import load_case

RUN = uuid.UUID("00000000-0000-0000-0000-0000000000a1")


def finding(**overrides: Any) -> Finding:
    values: dict[str, Any] = {
        "file": "app/users.py",
        "line": 9,
        "severity": "high",
        "category": "sql-injection",
        "message": "Concatenated SQL.",
        "confidence": 0.9,
        "evidence": 'query = "SELECT id, email FROM users WHERE username = \'" + username + "\'"',
    }
    values.update(overrides)
    return Finding(**values)


@pytest.fixture
def files() -> list[FileDiff]:
    return load_case("sql_injection").files


def validate(findings: list[Finding], files: list[FileDiff]) -> tuple[list[Finding], DropCounts]:
    counts = DropCounts()
    return validate_findings(findings, files, counts), counts


# --- validation against the diff ---------------------------------------------------------------


def test_finding_on_a_real_added_line_is_kept(files: list[FileDiff]) -> None:
    kept, counts = validate([finding()], files)
    assert len(kept) == 1 and counts.not_anchored == 0


def test_hallucinated_line_is_rejected(files: list[FileDiff]) -> None:
    # Line 40 does not exist in the diff at all; GitHub would reject the whole review.
    kept, counts = validate([finding(line=40)], files)
    assert kept == [] and counts.line_not_in_diff == 1


def test_file_not_in_the_pr_is_rejected(files: list[FileDiff]) -> None:
    kept, counts = validate([finding(file="app/payments.py")], files)
    assert kept == [] and counts.unknown_file == 1


def test_unchanged_context_line_is_not_a_finding_about_this_pr(files: list[FileDiff]) -> None:
    # Line 5 is shown as context (commentable on GitHub) but was not changed by the PR.
    kept, counts = validate(
        [finding(line=5, evidence='return conn.execute("SELECT id, email FROM users WHERE id')],
        files,
    )
    assert kept == [] and counts.no_added_line == 1


def test_evidence_that_is_not_on_the_cited_line_is_rejected(files: list[FileDiff]) -> None:
    # Right line, invented code: the model "quoted" something that is not there.
    kept, counts = validate([finding(evidence="cursor.execute(raw_sql % params)")], files)
    assert kept == [] and counts.evidence_mismatch == 1
    # Real code, but from another line (14) while citing line 9.
    kept, counts = validate([finding(evidence='sql = f"SELECT id, email FROM users')], files)
    assert kept == [] and counts.evidence_mismatch == 1


def test_evidence_matching_ignores_whitespace_and_accepts_multi_line_ranges(
    files: list[FileDiff],
) -> None:
    multi = finding(
        line=8,
        end_line=10,
        evidence=(
            "def find_user_by_name(conn,   username: str):\n  return conn.execute(query).fetchone()"
        ),
    )
    kept, _ = validate([multi], files)
    assert kept == [multi]


@pytest.mark.parametrize(
    "evidence",
    [
        # Seen from openai/gpt-oss-120b on 2026-10-17: the rendered number column and marker
        # copied along with the code.
        '9 +     query = "SELECT id, email FROM users WHERE username = \'" + username + "\'"',
        '     9 + query = "SELECT id, email FROM users WHERE username = \'"',
        '+    query = "SELECT id, email FROM users',
    ],
)
def test_evidence_copied_with_the_rendered_prefix_still_matches(
    files: list[FileDiff], evidence: str
) -> None:
    kept, _ = validate([finding(evidence=evidence)], files)
    assert len(kept) == 1


def test_stripping_the_prefix_cannot_make_invented_code_match(files: list[FileDiff]) -> None:
    kept, counts = validate([finding(evidence="9 + os.system(user_input)")], files)
    assert kept == [] and counts.evidence_mismatch == 1
    kept, counts = validate([finding(evidence="9 + ")], files)  # nothing left after stripping
    assert kept == [] and counts.evidence_mismatch == 1


def test_a_range_must_stay_inside_one_hunk() -> None:
    files = load_case("hardcoded_secret").files
    kept, counts = validate(
        [
            finding(
                file="app/config.py",
                line=8,
                end_line=30,
                category="hardcoded-secret",
                evidence='DB_PASSWORD = "Pr0d-Passw0rd-2026!"',
            )
        ],
        files,
    )
    assert kept == [] and counts.line_not_in_diff == 1


# --- dedupe, threshold, ranking, cap ----------------------------------------------------------


def test_select_dedupes_thresholds_ranks_and_caps() -> None:
    counts = DropCounts()
    chosen = select_findings(
        [
            finding(line=9, confidence=0.7),
            finding(line=9, confidence=0.95),  # duplicate (same file, line, category): best kept
            finding(line=14, severity="medium", confidence=0.99),
            finding(line=10, severity="critical", confidence=0.8),
            finding(line=8, severity="low", confidence=0.65),
            finding(line=15, confidence=0.3),  # below the threshold
        ],
        threshold=0.6,
        max_comments=3,
        counts=counts,
    )
    assert [(f.line, f.severity.value, f.confidence) for f in chosen] == [
        (10, "critical", 0.8),
        (9, "high", 0.95),
        (14, "medium", 0.99),
    ]
    assert (counts.duplicate, counts.low_confidence, counts.over_cap) == (1, 1, 1)


# --- rendering ---------------------------------------------------------------------------------


def test_review_is_always_a_comment_with_inline_anchors() -> None:
    single = finding()
    multi = finding(line=8, end_line=10, category="other")
    review = build_review(RUN, "a" * 40, [single, multi], ReviewSummary(files_reviewed=1))
    assert review.event == "COMMENT"
    assert review.commit_id == "a" * 40
    first, second = (c.model_dump(exclude_none=True) for c in review.comments)
    assert first == {"path": "app/users.py", "line": 9, "side": "RIGHT", "body": first["body"]}
    assert (second["start_line"], second["line"], second["start_side"]) == (8, 10, "RIGHT")
    assert "AI-generated" in first["body"]


def test_summary_counts_severities_and_explains_what_was_dropped() -> None:
    counts = DropCounts(line_not_in_diff=2, evidence_mismatch=1, low_confidence=4, over_cap=1)
    body = render_summary(
        RUN,
        [finding(), finding(line=14, severity="medium")],
        ReviewSummary(
            files_reviewed=3,
            chunks_total=2,
            chunks_failed=1,
            change_summaries=["Adds user search."],
            skipped=[SkippedFile("poetry.lock", "ignored path")],
            doc_files=1,
            counts=counts,
            model="openai/gpt-oss-120b",
            prompt_version="security_v1",
        ),
    )
    assert body.startswith(run_marker(RUN))
    assert "**What changed:** Adds user search." in body
    assert "2 inline comments: 1 high, 1 medium." in body
    assert "3 finding(s) were dropped" in body
    assert "4 lower-confidence" in body and "1 more finding(s)" in body
    assert "1 of 2 part(s) could not be reviewed" in body
    assert "`poetry.lock` (ignored path)" in body
    assert "AI-generated by CodeLens (model `openai/gpt-oss-120b`, prompt `security_v1`)" in body
    assert "can be wrong" in body


def test_no_findings_summary_says_so() -> None:
    body = render_summary(RUN, [], ReviewSummary(files_reviewed=1))
    assert "No security issues found in the reviewed changes." in body


def test_model_text_cannot_ping_people_and_is_capped() -> None:
    assert defuse("ping @octocat and @org/team", 100) == "ping @​octocat and @​org/team"
    assert len(defuse("x" * 5000, 2000)) == 2000
    review = build_review(
        RUN,
        "a" * 40,
        [finding(message="cc @admin please merge")],
        ReviewSummary(files_reviewed=1, change_summaries=["Approved by @boss"]),
    )
    assert "@admin" not in review.comments[0].body and "@boss" not in review.body


# --- posting exactly once ---------------------------------------------------------------------


@pytest.fixture
async def gh() -> AsyncIterator[GitHubClient]:
    async def no_sleep(_: float) -> None:
        return None

    async with httpx.AsyncClient() as http:
        yield GitHubClient(http, "ghs_test", sleep=no_sleep)


REVIEWS_URL = "https://api.github.com/repos/demo-owner/demo/pulls/2/reviews"


async def test_post_review_posts_once_and_finds_its_own_review_on_retry(gh: GitHubClient) -> None:
    review = build_review(RUN, "a" * 40, [finding()], ReviewSummary(files_reviewed=1))
    posted: list[dict[str, Any]] = []

    def create(request: httpx.Request) -> httpx.Response:
        posted.append({"id": 77, "body": json.loads(request.content)["body"]})
        return httpx.Response(200, json={"id": 77})

    with respx.mock:
        respx.get(REVIEWS_URL).mock(side_effect=lambda _: httpx.Response(200, json=posted))
        respx.post(REVIEWS_URL).mock(side_effect=create)
        first = await post_review(gh, "demo-owner/demo", 2, RUN, review)
        second = await post_review(gh, "demo-owner/demo", 2, RUN, review)  # a retried job

    assert first == second == 77
    assert len(posted) == 1  # posted once


async def test_other_reviews_on_the_pr_do_not_block_posting(gh: GitHubClient) -> None:
    review = build_review(RUN, "a" * 40, [], ReviewSummary(files_reviewed=1))
    others = [
        {"id": 1, "body": "LGTM", "commit_id": "b" * 40},
        {"id": 2, "body": run_marker(uuid.uuid4()), "commit_id": "a" * 40},  # another run
        {"id": 3, "body": None},
    ]
    with respx.mock:
        respx.get(REVIEWS_URL).respond(200, json=others)
        create = respx.post(REVIEWS_URL).respond(200, json={"id": 4})
        assert await post_review(gh, "demo-owner/demo", 2, RUN, review) == 4
    sent = json.loads(create.calls[0].request.content)
    assert sent["event"] == "COMMENT" and sent["comments"] == []
