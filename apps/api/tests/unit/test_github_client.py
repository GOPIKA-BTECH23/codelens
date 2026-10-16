"""GitHubClient: retries, rate limits, pagination and the request shapes (respx; no network).

Sleeping is faked: `sleeps` records every delay the client asked for, so the tests check
the waiting policy exactly and still run instantly.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx

from app.services.github_client import (
    GitHubClient,
    GitHubDiffTooLarge,
    GitHubError,
    GitHubForbidden,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubServerError,
    ReviewComment,
    ReviewRequest,
)

API = "https://api.github.com"
REPO = "octo-org/demo"
PR_URL = f"{API}/repos/{REPO}/pulls/7"
NOW = 1_800_000_000.0  # fake epoch seconds

PR_JSON = {
    "number": 7,
    "title": "Add feature",
    "state": "open",
    "draft": False,
    "merged": False,
    "user": {"login": "alice", "type": "User"},
    "head": {"ref": "feature", "sha": "a" * 40},
    "base": {"ref": "main", "sha": "b" * 40},
}


class Recorder:
    """Fake sleep plus a monotonic clock that only moves when the client sleeps."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []
        self.elapsed = 0.0

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.elapsed += seconds

    def monotonic(self) -> float:
        return self.elapsed


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def gh(http: httpx.AsyncClient, recorder: Recorder) -> GitHubClient:
    return GitHubClient(
        http,
        "ghs_test_token",
        sleep=recorder.sleep,
        clock=lambda: NOW,
        monotonic=recorder.monotonic,
        jitter=lambda: 0.0,
    )


# --- retries ---------------------------------------------------------------------------------


async def test_get_retries_on_502_with_exponential_backoff(
    gh: GitHubClient, recorder: Recorder, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(PR_URL).mock(
        side_effect=[httpx.Response(502), httpx.Response(502), httpx.Response(200, json=PR_JSON)]
    )
    pr = await gh.get_pull_request(REPO, 7)
    assert pr.head.sha == "a" * 40
    assert route.call_count == 3
    assert recorder.sleeps == [1.0, 2.0]


async def test_get_gives_up_after_max_retries(
    gh: GitHubClient, recorder: Recorder, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(PR_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(GitHubServerError, match="HTTP 503"):
        await gh.get_pull_request(REPO, 7)
    assert route.call_count == 4  # first try + 3 retries
    assert recorder.sleeps == [1.0, 2.0, 4.0]


async def test_get_retries_network_errors(gh: GitHubClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(PR_URL).mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json=PR_JSON)]
    )
    assert (await gh.get_pull_request(REPO, 7)).number == 7


async def test_review_post_is_never_retried_on_502(
    gh: GitHubClient, recorder: Recorder, respx_mock: respx.MockRouter
) -> None:
    """GitHub may have created the review before failing: a retry could post it twice."""
    route = respx_mock.post(f"{PR_URL}/reviews").mock(return_value=httpx.Response(502))
    with pytest.raises(GitHubServerError):
        await gh.create_review(REPO, 7, ReviewRequest(commit_id="a" * 40, body="summary"))
    assert route.call_count == 1
    assert recorder.sleeps == []


async def test_comment_post_is_not_retried_on_timeout(
    gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(f"{API}/repos/{REPO}/issues/7/comments").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    with pytest.raises(GitHubServerError, match="ReadTimeout"):
        await gh.create_issue_comment(REPO, 7, "hi")
    assert route.call_count == 1


# --- rate limits -----------------------------------------------------------------------------


async def test_honours_retry_after(
    gh: GitHubClient, recorder: Recorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(PR_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "7"}),
            httpx.Response(200, json=PR_JSON),
        ]
    )
    await gh.get_pull_request(REPO, 7)
    assert recorder.sleeps == [7.0]


async def test_primary_limit_waits_until_reset(
    gh: GitHubClient, recorder: Recorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(PR_URL).mock(
        side_effect=[
            httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 20)},
                json={"message": "API rate limit exceeded"},
            ),
            httpx.Response(200, json=PR_JSON),
        ]
    )
    await gh.get_pull_request(REPO, 7)
    assert recorder.sleeps == [21.0]  # until reset, plus one second for rounding


async def test_secondary_limit_without_headers_waits_a_minute(
    gh: GitHubClient, recorder: Recorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(PR_URL).mock(
        side_effect=[
            httpx.Response(403, json={"message": "You have exceeded a secondary rate limit."}),
            httpx.Response(200, json=PR_JSON),
        ]
    )
    await gh.get_pull_request(REPO, 7)
    assert recorder.sleeps == [60.0]


async def test_long_rate_limit_is_handed_back_to_the_job_queue(
    gh: GitHubClient, recorder: Recorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(PR_URL).mock(
        return_value=httpx.Response(
            429, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 1800)}
        )
    )
    with pytest.raises(GitHubRateLimited) as exc_info:
        await gh.get_pull_request(REPO, 7)
    assert recorder.sleeps == []  # did not park the worker for half an hour
    assert exc_info.value.retry_at.timestamp() == pytest.approx(NOW + 1801)


async def test_repeated_secondary_limits_grow_then_give_up(
    gh: GitHubClient, recorder: Recorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(PR_URL).mock(
        return_value=httpx.Response(403, json={"message": "secondary rate limit"})
    )
    with pytest.raises(GitHubRateLimited):
        await gh.get_pull_request(REPO, 7)
    assert recorder.sleeps == [60.0]  # the next wait (120 s) exceeds max_wait_seconds


async def test_review_post_is_retried_after_a_rate_limit(
    gh: GitHubClient, recorder: Recorder, respx_mock: respx.MockRouter
) -> None:
    """A rate-limited request was rejected, not executed, so retrying cannot duplicate."""
    route = respx_mock.post(f"{PR_URL}/reviews").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "3"}),
            httpx.Response(200, json={"id": 99}),
        ]
    )
    assert await gh.create_review(REPO, 7, ReviewRequest(commit_id="a" * 40, body="s")) == 99
    assert route.call_count == 2
    assert recorder.sleeps == [3.0]


async def test_plain_403_is_a_permission_error_not_a_rate_limit(
    gh: GitHubClient, recorder: Recorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(PR_URL).mock(
        return_value=httpx.Response(403, json={"message": "Resource not accessible by integration"})
    )
    with pytest.raises(GitHubForbidden):
        await gh.get_pull_request(REPO, 7)
    assert recorder.sleeps == []


async def test_writes_are_spaced_at_least_one_second_apart(
    http: httpx.AsyncClient, recorder: Recorder, respx_mock: respx.MockRouter
) -> None:
    gh = GitHubClient(http, "t", sleep=recorder.sleep, monotonic=recorder.monotonic)
    respx_mock.post(f"{API}/repos/{REPO}/issues/7/comments").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    await gh.create_issue_comment(REPO, 7, "one")
    await gh.create_issue_comment(REPO, 7, "two")
    assert recorder.sleeps == [1.0]  # no time passed between them, so a full second


# --- auth ------------------------------------------------------------------------------------


async def test_401_refreshes_the_token_once(
    http: httpx.AsyncClient, recorder: Recorder, respx_mock: respx.MockRouter
) -> None:
    tokens = iter(["ghs_revoked", "ghs_fresh"])
    current = {"token": next(tokens)}

    async def provider() -> str:
        return current["token"]

    def invalidate() -> None:
        current["token"] = next(tokens)

    route = respx_mock.get(PR_URL).mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json=PR_JSON)]
    )
    gh = GitHubClient(http, provider, on_unauthorized=invalidate, sleep=recorder.sleep)
    await gh.get_pull_request(REPO, 7)
    assert [c.request.headers["authorization"] for c in route.calls] == [
        "Bearer ghs_revoked",
        "Bearer ghs_fresh",
    ]


# --- endpoints -------------------------------------------------------------------------------


async def test_requests_carry_the_standard_headers(
    gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(PR_URL).respond(200, json=PR_JSON)
    await gh.get_pull_request(REPO, 7)
    headers = route.calls.last.request.headers
    assert headers["authorization"] == "Bearer ghs_test_token"
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["x-github-api-version"]
    assert headers["user-agent"] == "CodeLens"


async def test_diff_uses_the_diff_media_type(
    gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    diff = "diff --git a/x.py b/x.py\n+print('hi')\n"
    route = respx_mock.get(PR_URL).respond(200, text=diff)
    assert await gh.get_pull_request_diff(REPO, 7) == diff
    assert route.calls.last.request.headers["accept"] == "application/vnd.github.diff"


async def test_unrenderable_diff_raises_diff_too_large(
    gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(PR_URL).respond(406, json={"message": "diff too large"})
    with pytest.raises(GitHubDiffTooLarge):
        await gh.get_pull_request_diff(REPO, 7)


async def test_list_files_follows_link_headers(
    gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    page2 = f"{API}/repositories/1/pulls/7/files?per_page=100&page=2"
    route1 = respx_mock.get(f"{PR_URL}/files", params={"per_page": "100"}).respond(
        200,
        json=[{"filename": "a.py", "status": "modified", "patch": "@@ -1 +1 @@"}],
        headers={"link": f'<{page2}>; rel="next", <{page2}>; rel="last"'},
    )
    route2 = respx_mock.get(page2).respond(
        200,
        json=[{"filename": "logo.png", "status": "added"}],  # binary: no patch
    )
    files = await gh.list_pull_request_files(REPO, 7)
    assert [f.filename for f in files] == ["a.py", "logo.png"]
    assert files[1].patch is None
    assert route1.called and route2.called


async def test_file_content_returns_raw_bytes_at_a_ref(
    gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(f"{API}/repos/{REPO}/contents/src/my%20file.py").respond(
        200, content=b"print('x')\n"
    )
    assert await gh.get_file_content(REPO, "src/my file.py", "abc123") == b"print('x')\n"
    request = route.calls.last.request
    assert request.url.params["ref"] == "abc123"
    assert request.headers["accept"] == "application/vnd.github.raw+json"


async def test_missing_file_raises_not_found(
    gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{API}/repos/{REPO}/contents/nope.py").respond(404)
    with pytest.raises(GitHubNotFound):
        await gh.get_file_content(REPO, "nope.py", "main")


async def test_repository_name_must_be_owner_slash_name(gh: GitHubClient) -> None:
    with pytest.raises(ValueError):
        await gh.get_pull_request("octo-org/demo/../../admin", 7)


async def test_tarball_follows_the_redirect_without_our_token(
    gh: GitHubClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    download = "https://codeload.github.com/octo-org/demo/legacy.tar.gz/abc?token=TEMP"
    respx_mock.get(f"{API}/repos/{REPO}/tarball/abc").respond(302, headers={"location": download})
    route = respx_mock.get(download).respond(200, content=b"x" * 1000)

    target = tmp_path / "repo.tar.gz"
    assert await gh.download_tarball(REPO, "abc", target) == 1000
    assert target.read_bytes() == b"x" * 1000
    assert "authorization" not in route.calls.last.request.headers


async def test_oversized_tarball_is_aborted_and_removed(
    gh: GitHubClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    download = "https://codeload.github.com/octo-org/demo/legacy.tar.gz/abc"
    respx_mock.get(f"{API}/repos/{REPO}/tarball/abc").respond(302, headers={"location": download})
    respx_mock.get(download).respond(200, content=b"x" * 5000)

    target = tmp_path / "repo.tar.gz"
    with pytest.raises(GitHubError, match="exceeds"):
        await gh.download_tarball(REPO, "abc", target, max_bytes=1000)
    assert not target.exists()


async def test_create_review_sends_one_batched_review(
    gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(f"{PR_URL}/reviews").respond(200, json={"id": 555})
    review = ReviewRequest(
        commit_id="a" * 40,
        body="CodeLens summary",
        comments=[
            ReviewComment(path="app.py", line=12, body="SQL injection"),
            ReviewComment(path="app.py", line=20, start_line=18, start_side="RIGHT", body="x"),
        ],
    )
    assert await gh.create_review(REPO, 7, review) == 555
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "commit_id": "a" * 40,
        "body": "CodeLens summary",
        "event": "COMMENT",
        "comments": [
            {"path": "app.py", "line": 12, "body": "SQL injection", "side": "RIGHT"},
            {
                "path": "app.py",
                "line": 20,
                "start_line": 18,
                "start_side": "RIGHT",
                "body": "x",
                "side": "RIGHT",
            },
        ],
    }


async def test_create_issue_comment(gh: GitHubClient, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{API}/repos/{REPO}/issues/7/comments").respond(201, json={"id": 8})
    assert await gh.create_issue_comment(REPO, 7, "Skipped 2 binary files") == 8
    assert json.loads(route.calls.last.request.content) == {"body": "Skipped 2 binary files"}
