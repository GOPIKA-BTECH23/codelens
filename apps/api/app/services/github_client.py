"""GitHub REST calls made with an INSTALLATION token (worker only).

Verified against GitHub's docs on 2026-10-16:
- Rate limits ("Rate limits for the REST API", "Best practices"): primary and secondary
  limits both answer 403 OR 429. Wait for `retry-after` seconds if present; else, if
  `x-ratelimit-remaining` is 0, until `x-ratelimit-reset` (epoch seconds); else at least one
  minute, growing exponentially if it keeps happening. Leave >= 1 s between mutating
  requests. Follow the `Link` header for pagination; never build page URLs yourself.
- Pulls: `Accept: application/vnd.github.diff` returns the unified diff (406 is listed for
  diffs GitHub will not render); "list pull request files" pages at most 100 and returns at
  most 3000 files; `patch` is optional (binary and very large files have none).
- Contents: `application/vnd.github.raw+json` returns the raw bytes (files up to 100 MB).
- Tarball: GET /repos/{o}/{r}/tarball/{ref} answers 302 to a temporary download URL
  (private repos: valid five minutes).
- Reviews: POST /repos/{o}/{r}/pulls/{n}/reviews with commit_id, body, event and
  comments[{path, line, side, start_line, start_side, body}]. Issue comments: POST
  /repos/{o}/{r}/issues/{n}/comments (a PR is an issue). Both need "Pull requests: write".

Retry policy (see ADR 0003):
- GETs are retried on 5xx and network errors with exponential backoff and jitter.
- POSTs that CREATE something are NOT retried on 5xx or network errors: GitHub may have
  created the review before the error, and a retry would post it twice. They ARE retried
  after a rate-limit answer, because then GitHub rejected the request without acting on it.
- A rate-limit wait longer than `max_wait_seconds` is not slept: GitHubRateLimited carries
  `retry_at` so the job queue can reschedule the job instead of parking a worker.
"""

import asyncio
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel

from app.services.github_app import GitHubApp
from app.services.github_common import GITHUB_API, api_headers

JSON = "application/vnd.github+json"
DIFF = "application/vnd.github.diff"
RAW = "application/vnd.github.raw+json"

# "owner/name" exactly; anything else could smuggle extra path segments into the URL.
_FULL_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# 3000 files / 100 per page = 30 pages; the cap stops a misbehaving Link header looping us.
_PER_PAGE = 100
_MAX_PAGES = 30
# Matches the 512 MB RAM / small-disk free tier (decision 5); the M8 indexer can raise it.
DEFAULT_MAX_TARBALL_BYTES = 200 * 1024 * 1024
MIN_SECONDS_BETWEEN_WRITES = 1.0

logger = logging.getLogger("app.github")

TokenProvider = Callable[[], Awaitable[str]]


# --- errors --------------------------------------------------------------------------------


class GitHubError(Exception):
    """A GitHub call failed. Messages are safe to log (no tokens, no URLs with tokens)."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitHubNotFound(GitHubError):
    """404: wrong path, or the installation cannot see the repository any more."""


class GitHubUnauthorized(GitHubError):
    """401 even with a freshly minted token."""


class GitHubForbidden(GitHubError):
    """403 that is not a rate limit: the App lacks a permission."""


class GitHubServerError(GitHubError):
    """5xx or a network failure that retries did not cure (or that we must not retry)."""


class GitHubDiffTooLarge(GitHubError):
    """GitHub will not render this PR's diff; use list_pull_request_files instead."""


class GitHubRateLimited(GitHubError):
    """Rate limited for longer than we are willing to sleep. Retry at `retry_at`."""

    def __init__(self, message: str, *, status: int, retry_at: datetime) -> None:
        super().__init__(message, status=status)
        self.retry_at = retry_at


# --- data shapes ---------------------------------------------------------------------------


class GitRef(BaseModel):
    ref: str
    sha: str


class PullRequestUser(BaseModel):
    login: str
    type: str = "User"


class PullRequestDetails(BaseModel):
    number: int
    title: str
    state: str
    draft: bool = False
    merged: bool = False
    user: PullRequestUser
    head: GitRef
    base: GitRef


class PullRequestFile(BaseModel):
    filename: str
    status: str
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    patch: str | None = None  # absent for binary and very large files (decision 15)
    previous_filename: str | None = None


class ReviewComment(BaseModel):
    """One inline comment, anchored by file line on one side of the diff (decision 16)."""

    path: str
    line: int
    body: str
    side: Literal["RIGHT", "LEFT"] = "RIGHT"
    start_line: int | None = None  # set for a multi-line comment
    start_side: Literal["RIGHT", "LEFT"] | None = None


class ReviewRequest(BaseModel):
    commit_id: str  # the head SHA the review was computed for
    body: str
    event: Literal["COMMENT", "REQUEST_CHANGES", "APPROVE"] = "COMMENT"
    comments: list[ReviewComment] = []


# --- rate limits ---------------------------------------------------------------------------


def rate_limit_wait(response: httpx.Response, *, now: float, hits: int) -> float | None:
    """Seconds to wait if `response` is a rate-limit answer, else None.

    `hits` is how many rate-limit answers this request already got (0 for the first), used
    for the exponential growth GitHub asks for when no header says how long to wait.
    """
    if response.status_code not in (403, 429):
        return None
    retry_after = response.headers.get("retry-after")
    remaining = response.headers.get("x-ratelimit-remaining")
    reset = response.headers.get("x-ratelimit-reset")
    is_limit = (
        response.status_code == 429
        or retry_after is not None
        or remaining == "0"
        or "rate limit" in response.text.lower()
    )
    if not is_limit:
        return None  # an ordinary 403: missing permission
    if retry_after is not None and retry_after.strip().isdigit():
        return float(retry_after)
    if remaining == "0" and reset is not None and reset.strip().isdigit():
        return max(float(reset) - now, 0.0) + 1.0  # +1 s: reset is rounded down
    return 60.0 * float(2**hits)


# --- client --------------------------------------------------------------------------------


class GitHubClient:
    """Async wrapper over the handful of REST endpoints the worker needs.

    Args:
        http: a shared httpx.AsyncClient (the caller owns its lifetime).
        token: an installation token, or an async callable that returns a current one
            (preferred: the token cache refreshes it before it expires mid-job).
        on_unauthorized: called on a 401 before ONE retry with a newly fetched token.
        sleep / clock / monotonic / jitter: injectable so tests run instantly.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        token: str | TokenProvider,
        *,
        on_unauthorized: Callable[[], None] | None = None,
        max_retries: int = 3,
        max_wait_seconds: float = 60.0,
        backoff_base_seconds: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._http = http
        self._token = token
        self._on_unauthorized = on_unauthorized
        self._max_retries = max_retries
        self._max_wait = max_wait_seconds
        self._backoff_base = backoff_base_seconds
        self._sleep = sleep
        self._clock = clock
        self._monotonic = monotonic
        self._jitter = jitter
        self._last_write: float | None = None

    # --- pull requests -------------------------------------------------------------------

    async def get_pull_request(self, repo: str, number: int) -> PullRequestDetails:
        response = await self._request("GET", f"{_repo_url(repo)}/pulls/{number}")
        return PullRequestDetails.model_validate(response.json())

    async def get_pull_request_diff(self, repo: str, number: int) -> str:
        """The PR's unified diff. Raises GitHubDiffTooLarge when GitHub will not render it."""
        try:
            response = await self._request("GET", f"{_repo_url(repo)}/pulls/{number}", accept=DIFF)
        except GitHubError as exc:
            if exc.status == 406:
                raise GitHubDiffTooLarge(
                    f"diff of {repo}#{number} is too large", status=406
                ) from exc
            raise
        return response.text

    async def list_pull_request_files(self, repo: str, number: int) -> list[PullRequestFile]:
        items = await self._paginate(f"{_repo_url(repo)}/pulls/{number}/files")
        return [PullRequestFile.model_validate(item) for item in items]

    # --- contents ------------------------------------------------------------------------

    async def get_file_content(self, repo: str, path: str, ref: str) -> bytes:
        """Raw bytes of one file at `ref` (a SHA, branch or tag)."""
        url = f"{_repo_url(repo)}/contents/{quote(path.lstrip('/'), safe='/')}"
        response = await self._request("GET", url, accept=RAW, params={"ref": ref})
        return response.content

    async def download_tarball(
        self,
        repo: str,
        ref: str,
        destination: Path,
        *,
        max_bytes: int = DEFAULT_MAX_TARBALL_BYTES,
    ) -> int:
        """Stream the repo archive at `ref` to `destination` (never into memory). Returns
        the number of bytes written; deletes the partial file on any failure.

        The API answers 302 to a temporary download URL. We follow it ourselves so the
        installation token is never sent to the download host, and the URL (it contains a
        short-lived access token of its own) is never logged.
        """
        response = await self._request("GET", f"{_repo_url(repo)}/tarball/{quote(ref, safe='')}")
        if response.status_code != 302 or "location" not in response.headers:
            raise GitHubError(
                f"tarball request returned HTTP {response.status_code}, expected a redirect",
                status=response.status_code,
            )
        written = 0
        try:
            async with self._http.stream("GET", response.headers["location"]) as download:
                if download.status_code != 200:
                    raise GitHubError(
                        f"tarball download returned HTTP {download.status_code}",
                        status=download.status_code,
                    )
                with destination.open("wb") as out:
                    async for chunk in download.aiter_bytes():
                        written += len(chunk)
                        if written > max_bytes:
                            raise GitHubError(f"tarball of {repo} exceeds {max_bytes} bytes")
                        out.write(chunk)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return written

    # --- writes --------------------------------------------------------------------------

    async def create_review(self, repo: str, number: int, review: ReviewRequest) -> int:
        """Post ONE review (summary + inline comments) in a single call. Returns its id."""
        response = await self._request(
            "POST",
            f"{_repo_url(repo)}/pulls/{number}/reviews",
            json=review.model_dump(exclude_none=True),
            idempotent=False,
        )
        return int(response.json()["id"])

    async def create_issue_comment(self, repo: str, number: int, body: str) -> int:
        """A plain comment on the PR conversation (a PR is an issue). Returns its id."""
        response = await self._request(
            "POST",
            f"{_repo_url(repo)}/issues/{number}/comments",
            json={"body": body},
            idempotent=False,
        )
        return int(response.json()["id"])

    # --- plumbing ------------------------------------------------------------------------

    async def _paginate(self, url: str) -> list[Any]:
        """Follow `Link: rel="next"` until it ends (or the page cap)."""
        items: list[Any] = []
        next_url: str | None = url
        params: Mapping[str, str | int] | None = {"per_page": _PER_PAGE}
        for _ in range(_MAX_PAGES):
            if next_url is None:
                return items
            response = await self._request("GET", next_url, params=params)
            page = response.json()
            if not isinstance(page, list):
                raise GitHubError(f"expected a list from {url}", status=response.status_code)
            items.extend(page)
            next_url = response.links.get("next", {}).get("url")
            params = None  # the next URL already carries every query parameter
        if next_url is not None:
            logger.warning("pagination cap reached", extra={"url": url, "pages": _MAX_PAGES})
        return items

    async def _current_token(self) -> str:
        return self._token if isinstance(self._token, str) else await self._token()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        accept: str = JSON,
        params: Mapping[str, str | int] | None = None,
        json: Any = None,
        idempotent: bool = True,
    ) -> httpx.Response:
        """One logical request with retries. Returns any 2xx/3xx response.

        `idempotent=False` (creating a review or comment) turns off retries on 5xx and
        network errors; rate-limit retries stay on.
        """
        attempt = 0  # retries used so far (5xx, network, rate limit)
        limit_hits = 0
        reauthenticated = False
        while True:
            if not idempotent:
                await self._space_out_writes()
            headers = api_headers(await self._current_token(), accept=accept)
            try:
                response = await self._http.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=headers,
                    follow_redirects=False,  # the tarball redirect is handled explicitly
                )
            except httpx.TransportError as exc:
                if not idempotent or attempt >= self._max_retries:
                    raise GitHubServerError(
                        f"{method} {_path(url)} failed: {type(exc).__name__}"
                    ) from exc
                await self._backoff(attempt, method, url, type(exc).__name__)
                attempt += 1
                continue

            status = response.status_code
            if status == 401 and not reauthenticated and self._on_unauthorized is not None:
                # The cached token may have been revoked early; mint a new one, once.
                self._on_unauthorized()
                reauthenticated = True
                continue

            wait = rate_limit_wait(response, now=self._clock(), hits=limit_hits)
            if wait is not None:
                if attempt >= self._max_retries or wait > self._max_wait:
                    retry_at = datetime.fromtimestamp(self._clock() + wait, UTC)
                    raise GitHubRateLimited(
                        f"{method} {_path(url)} rate limited; retry after {wait:.0f}s",
                        status=status,
                        retry_at=retry_at,
                    )
                logger.warning(
                    "github rate limited, waiting",
                    extra={"method": method, "path": _path(url), "wait_s": wait, "status": status},
                )
                await self._sleep(wait)
                attempt += 1
                limit_hits += 1
                continue

            if status >= 500:
                if not idempotent or attempt >= self._max_retries:
                    raise GitHubServerError(
                        f"{method} {_path(url)} returned HTTP {status}", status=status
                    )
                await self._backoff(attempt, method, url, f"HTTP {status}")
                attempt += 1
                continue

            if status >= 400:
                raise _error_for(method, url, response)
            logger.debug(
                "github request",
                extra={
                    "method": method,
                    "path": _path(url),
                    "status": status,
                    "ratelimit_remaining": response.headers.get("x-ratelimit-remaining"),
                },
            )
            return response

    async def _backoff(self, attempt: int, method: str, url: str, reason: str) -> None:
        """base * 2^attempt plus up to one base of random jitter: ~1 s, 2 s, 4 s."""
        delay = self._backoff_base * (2**attempt) + self._backoff_base * self._jitter()
        logger.warning(
            "github request failed, retrying",
            extra={"method": method, "path": _path(url), "reason": reason, "delay_s": delay},
        )
        await self._sleep(delay)

    async def _space_out_writes(self) -> None:
        """GitHub asks for at least one second between mutating requests (secondary limits)."""
        if self._last_write is not None:
            gap = self._monotonic() - self._last_write
            if gap < MIN_SECONDS_BETWEEN_WRITES:
                await self._sleep(MIN_SECONDS_BETWEEN_WRITES - gap)
        self._last_write = self._monotonic()


def installation_client(
    app: GitHubApp, installation_id: int, http: httpx.AsyncClient, **kwargs: Any
) -> GitHubClient:
    """A client for one installation whose token comes from (and refreshes through) the
    App's token cache. This is how the worker builds clients."""
    return GitHubClient(
        http,
        lambda: app.installation_token(installation_id),
        on_unauthorized=lambda: app.invalidate(installation_id),
        **kwargs,
    )


def _repo_url(repo: str) -> str:
    if not _FULL_NAME.match(repo):
        raise ValueError("repository must be 'owner/name'")
    return f"{GITHUB_API}/repos/{repo}"


def _path(url: str) -> str:
    """URL without scheme, host or query, for logs."""
    return httpx.URL(url).path


def _error_for(method: str, url: str, response: httpx.Response) -> GitHubError:
    status = response.status_code
    message = f"{method} {_path(url)} returned HTTP {status}"
    if status == 404:
        return GitHubNotFound(message, status=status)
    if status == 401:
        return GitHubUnauthorized(message, status=status)
    if status == 403:
        return GitHubForbidden(message, status=status)
    return GitHubError(message, status=status)
