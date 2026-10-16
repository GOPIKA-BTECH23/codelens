"""A fake GitHub for the WORKER side (installation token, PR, files, reviews), on respx.

It behaves like GitHub where it matters for the review pipeline: creating a review with an
inline comment on a line that is not part of the diff answers 422 for the whole review, just
as the real API does. So a test passes only if every comment we post is anchored correctly.

Not named test_*: pytest would collect it.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import respx

from app.services.diff import from_pr_files
from app.services.github_client import PullRequestFile

API = "https://api.github.com"


@dataclass
class FakePR:
    repo: str
    number: int
    head_sha: str
    files: list[dict[str, Any]]
    state: str = "open"
    reviews: list[dict[str, Any]] = field(default_factory=list)
    posted: list[dict[str, Any]] = field(default_factory=list)  # bodies we accepted


class FakeGitHubApp:
    def __init__(self, mock: respx.MockRouter, installation_id: int) -> None:
        self.prs: dict[tuple[str, int], FakePR] = {}
        self.token_requests = 0
        self.fail_pr_with: int | None = None
        mock.post(f"{API}/app/installations/{installation_id}/access_tokens").mock(
            side_effect=self._token
        )
        base = rf"{API}/repos/(?P<repo>[^/]+/[^/]+)/pulls/(?P<number>\d+)"
        mock.get(url__regex=rf"{base}$").mock(side_effect=self._pr)
        mock.get(url__regex=rf"{base}/files").mock(side_effect=self._files)
        mock.get(url__regex=rf"{base}/reviews").mock(side_effect=self._list_reviews)
        mock.post(url__regex=rf"{base}/reviews").mock(side_effect=self._create_review)

    def add(self, pr: FakePR) -> FakePR:
        self.prs[(pr.repo, pr.number)] = pr
        return pr

    # --- handlers ------------------------------------------------------------------------------

    def _token(self, request: httpx.Request) -> httpx.Response:
        self.token_requests += 1
        assert request.headers["authorization"].startswith("Bearer ")  # the App JWT
        return httpx.Response(
            201, json={"token": "ghs_fake_installation_token", "expires_at": "2099-01-01T00:00:00Z"}
        )

    def _get(self, repo: str, number: str) -> FakePR | None:
        return self.prs.get((repo, int(number)))

    def _pr(self, request: httpx.Request, repo: str, number: str) -> httpx.Response:
        self._check_token(request)
        if self.fail_pr_with is not None:
            return httpx.Response(self.fail_pr_with, json={"message": "nope"})
        pr = self._get(repo, number)
        if pr is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(
            200,
            json={
                "number": pr.number,
                "title": "Seeded PR",
                "state": pr.state,
                "draft": False,
                "merged": False,
                "user": {"login": "demo-contributor", "type": "User"},
                "head": {"ref": "feature", "sha": pr.head_sha},
                "base": {"ref": "main", "sha": "b" * 40},
            },
        )

    def _files(self, request: httpx.Request, repo: str, number: str) -> httpx.Response:
        self._check_token(request)
        pr = self._get(repo, number)
        return httpx.Response(200, json=pr.files if pr else [])

    def _list_reviews(self, request: httpx.Request, repo: str, number: str) -> httpx.Response:
        self._check_token(request)
        pr = self._get(repo, number)
        return httpx.Response(200, json=pr.reviews if pr else [])

    def _create_review(self, request: httpx.Request, repo: str, number: str) -> httpx.Response:
        self._check_token(request)
        pr = self._get(repo, number)
        if pr is None:
            return httpx.Response(404)
        body = json.loads(request.content)
        if body.get("event") == "COMMENT" and not body.get("body"):
            return httpx.Response(422, json={"message": "body is required for COMMENT"})
        commentable = {
            f.path: f.commentable_lines
            for f in from_pr_files(PullRequestFile.model_validate(x) for x in pr.files)
        }
        for comment in body.get("comments", []):
            lines = {comment["line"], comment.get("start_line", comment["line"])}
            if comment.get("side", "RIGHT") != "RIGHT" or not lines <= commentable.get(
                comment["path"], frozenset()
            ):
                # Real GitHub: "Pull request review thread line must be part of the diff".
                return httpx.Response(422, json={"message": "line must be part of the diff"})
        review_id = 1000 + len(pr.reviews)
        pr.reviews.append({"id": review_id, "body": body["body"], "commit_id": body["commit_id"]})
        pr.posted.append(body)
        return httpx.Response(200, json={"id": review_id})

    @staticmethod
    def _check_token(request: httpx.Request) -> None:
        assert request.headers["authorization"] == "Bearer ghs_fake_installation_token"
