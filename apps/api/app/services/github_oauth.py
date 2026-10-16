"""GitHub calls made with a USER access token during login (and only during login).

Verified against GitHub's docs on 2026-10-15 ("Generating a user access token for a GitHub
App", REST "Installations" and "Organization members"):
- authorize: GET https://github.com/login/oauth/authorize with client_id, redirect_uri,
  state, code_challenge + code_challenge_method=S256 (PKCE, "strongly recommended");
- exchange:  POST https://github.com/login/oauth/access_token; needs Accept:
  application/json (the default is form-encoded) and returns HTTP 200 even for errors,
  with an `error` field in the body;
- GET /user/installations: installations of OUR app the user can access;
- GET /user/installations/{id}/repositories: the repos the user can access in one;
- GET /user/memberships/orgs/{org}: role admin|member; needs the App's
  "Organization -> Members: read" permission (403 without it).

This module never logs or stores the token. The caller revokes it when done.
"""

import logging
from typing import Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from app.config import Settings
from app.services.github_common import GITHUB_API, GITHUB_WEB, api_headers

_PER_PAGE = 100
# A user with more than 50 pages (5000) of installations or repos is not a realistic case;
# the cap keeps a misbehaving API from looping us forever.
_MAX_PAGES = 50

logger = logging.getLogger("app.github")


class GitHubOAuthError(Exception):
    """Login cannot continue. The message is safe to log (never contains a token)."""


class GitHubUser(BaseModel):
    id: int
    login: str
    name: str | None = None
    email: str | None = None
    avatar_url: str | None = None


class GitHubAccount(BaseModel):
    # Enterprise accounts have no login/type; we skip those installations.
    id: int
    login: str | None = None
    type: str | None = None  # "User" | "Organization"


class GitHubInstallation(BaseModel):
    id: int
    account: GitHubAccount


class GitHubRepo(BaseModel):
    id: int
    full_name: str
    private: bool
    default_branch: str = "main"


OrgRole = Literal["admin", "member"]


def authorize_url(settings: Settings, *, state: str, code_challenge: str) -> str:
    """Where to send the browser to start the login."""
    query = urlencode(
        {
            "client_id": settings.github_oauth_client_id,
            "redirect_uri": settings.oauth_redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{GITHUB_WEB}/login/oauth/authorize?{query}"


class GitHubOAuthClient:
    """Thin async wrapper over the handful of endpoints login needs."""

    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self._http = http
        self._settings = settings

    async def exchange_code(self, code: str, code_verifier: str) -> str:
        """Trade the one-time `code` (plus the PKCE verifier) for a user access token."""
        try:
            response = await self._http.post(
                f"{GITHUB_WEB}/login/oauth/access_token",
                # Sent in the form body, not the query string, so the client secret and code
                # never appear in any URL (URLs end up in proxy and client logs).
                data={
                    "client_id": self._settings.github_oauth_client_id,
                    "client_secret": self._settings.github_oauth_client_secret.get_secret_value(),
                    "code": code,
                    "redirect_uri": self._settings.oauth_redirect_uri,
                    "code_verifier": code_verifier,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise GitHubOAuthError(f"token exchange failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise GitHubOAuthError(f"token exchange returned HTTP {response.status_code}")
        body = response.json()
        # GitHub reports OAuth errors (bad_verification_code, redirect_uri_mismatch,
        # incorrect_client_credentials, ...) with HTTP 200 and an `error` field.
        if "error" in body:
            raise GitHubOAuthError(f"token exchange error: {body['error']}")
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise GitHubOAuthError("token exchange returned no access_token")
        return token

    async def get_user(self, token: str) -> GitHubUser:
        response = await self._api_get(token, "/user")
        return GitHubUser.model_validate(response.json())

    async def list_installations(self, token: str) -> list[GitHubInstallation]:
        items = await self._paginate(token, "/user/installations", "installations")
        return [GitHubInstallation.model_validate(item) for item in items]

    async def list_installation_repos(self, token: str, installation_id: int) -> list[GitHubRepo]:
        items = await self._paginate(
            token, f"/user/installations/{installation_id}/repositories", "repositories"
        )
        return [GitHubRepo.model_validate(item) for item in items]

    async def get_org_role(self, token: str, org: str) -> OrgRole | None:
        """The user's ACTIVE role in `org`, or None when GitHub does not tell us.

        None covers: not an org member (404; access comes via a repo collaboration),
        a pending invitation, and a missing "Members: read" permission (403).
        """
        response = await self._api_get(token, f"/user/memberships/orgs/{org}", allow={403, 404})
        if response.status_code == 403:
            logger.warning(
                "org role unavailable; does the GitHub App have Members: read?",
                extra={"org": org},
            )
            return None
        if response.status_code == 404:
            return None
        body = response.json()
        if body.get("state") != "active":
            return None
        return "admin" if body.get("role") == "admin" else "member"

    async def revoke_token(self, token: str) -> None:
        """Best effort: tell GitHub to invalidate the user token now that login is done.

        We never store it, and it would expire on its own after 8 hours; revoking closes
        that window. App-owned endpoint: Basic auth with the client id and secret.
        A failure is logged and swallowed, because the login itself already succeeded.
        """
        client_id = self._settings.github_oauth_client_id
        try:
            response = await self._http.request(
                "DELETE",
                f"{GITHUB_API}/applications/{client_id}/token",
                json={"access_token": token},
                auth=(client_id, self._settings.github_oauth_client_secret.get_secret_value()),
                headers=api_headers(),
            )
        except httpx.HTTPError as exc:
            logger.warning("user token revocation failed", extra={"error": type(exc).__name__})
            return
        if response.status_code != 204:
            logger.warning("user token revocation failed", extra={"status": response.status_code})

    # --- helpers -------------------------------------------------------------------------

    async def _api_get(
        self,
        token: str,
        path_or_url: str,
        *,
        params: dict[str, int] | None = None,
        allow: frozenset[int] | set[int] = frozenset(),
    ) -> httpx.Response:
        url = path_or_url if path_or_url.startswith("https://") else f"{GITHUB_API}{path_or_url}"
        try:
            response = await self._http.get(url, params=params, headers=api_headers(token))
        except httpx.HTTPError as exc:
            raise GitHubOAuthError(f"GET {path_or_url} failed: {type(exc).__name__}") from exc
        if response.status_code != 200 and response.status_code not in allow:
            raise GitHubOAuthError(f"GET {path_or_url} returned HTTP {response.status_code}")
        return response

    async def _paginate(self, token: str, path: str, key: str) -> list[dict[str, object]]:
        """Follow GitHub's Link: rel="next" headers and collect `key` from every page."""
        items: list[dict[str, object]] = []
        response = await self._api_get(token, path, params={"per_page": _PER_PAGE})
        for _ in range(_MAX_PAGES):
            items.extend(response.json()[key])
            next_url = response.links.get("next", {}).get("url")
            if not next_url:
                return items
            if not next_url.startswith(f"{GITHUB_API}/"):
                # Never send the user's token to a host GitHub did not promise.
                raise GitHubOAuthError("pagination link points outside api.github.com")
            response = await self._api_get(token, next_url)
        raise GitHubOAuthError(f"GET {path} exceeded {_MAX_PAGES} pages")
