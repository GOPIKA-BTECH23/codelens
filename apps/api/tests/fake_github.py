"""A fake GitHub for login tests, built on respx (which intercepts httpx).

Each fake user has a one-time `code` and a user token. Every API route looks at the
Authorization header to decide whose data to return, so several users can log in within
one test, like on the real GitHub.
"""

import json
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import parse_qs, urlparse

import httpx
import respx
from fastapi.testclient import TestClient

from tests.app_utils import TEST_CLIENT_ID, TEST_CLIENT_SECRET

GITHUB_API = "https://api.github.com"
TOKEN_URL = "https://github.com/login/oauth/access_token"


@dataclass
class FakeRepo:
    id: int
    full_name: str
    private: bool = False

    def as_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "full_name": self.full_name,
            "private": self.private,
            "default_branch": "main",
        }


@dataclass
class FakeInstallation:
    id: int
    account_login: str
    account_id: int
    account_type: Literal["User", "Organization"] = "User"

    def as_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "account": {
                "login": self.account_login,
                "id": self.account_id,
                "type": self.account_type,
            },
        }


@dataclass
class FakeUser:
    id: int
    login: str
    name: str | None = None
    # installation -> repos of it this user can see
    installations: dict[int, tuple[FakeInstallation, list[FakeRepo]]] = field(default_factory=dict)
    # org login -> "admin" | "member" | "forbidden" (App lacks Members: read) ; absent = 404
    org_roles: dict[str, str] = field(default_factory=dict)

    @property
    def code(self) -> str:
        return f"code-{self.login}"

    @property
    def token(self) -> str:
        return f"ghu_{self.login}_SECRET_TOKEN_VALUE"

    def grant(self, installation: FakeInstallation, *repos: FakeRepo) -> "FakeUser":
        self.installations[installation.id] = (installation, list(repos))
        return self


class FakeGitHub:
    def __init__(self, mock: respx.MockRouter) -> None:
        self.users: dict[str, FakeUser] = {}
        self.exchanges: list[dict[str, str]] = []  # form bodies sent to the token endpoint
        self.revoked: list[str] = []
        self.token_route = mock.post(TOKEN_URL).mock(side_effect=self._exchange)
        mock.get(f"{GITHUB_API}/user").mock(side_effect=self._user)
        mock.get(f"{GITHUB_API}/user/installations").mock(side_effect=self._installations)
        mock.get(url__regex=rf"{GITHUB_API}/user/installations/(?P<inst_id>\d+)/repositories").mock(
            side_effect=self._repos
        )
        mock.get(url__regex=rf"{GITHUB_API}/user/memberships/orgs/(?P<org>[^/]+)$").mock(
            side_effect=self._org_membership
        )
        mock.delete(f"{GITHUB_API}/applications/{TEST_CLIENT_ID}/token").mock(
            side_effect=self._revoke
        )

    def add(self, user: FakeUser) -> FakeUser:
        self.users[user.token] = user
        return user

    # --- handlers --------------------------------------------------------------------------

    def _caller(self, request: httpx.Request) -> FakeUser | None:
        auth = request.headers.get("authorization", "")
        return self.users.get(auth.removeprefix("Bearer "))

    def _exchange(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.exchanges.append(form)
        if form.get("client_secret") != TEST_CLIENT_SECRET:
            return httpx.Response(200, json={"error": "incorrect_client_credentials"})
        for user in self.users.values():
            if form.get("code") == user.code:
                return httpx.Response(
                    200, json={"access_token": user.token, "token_type": "bearer", "scope": ""}
                )
        # Like GitHub: HTTP 200 with an error field.
        return httpx.Response(200, json={"error": "bad_verification_code"})

    def _user(self, request: httpx.Request) -> httpx.Response:
        user = self._caller(request)
        if user is None:
            return httpx.Response(401, json={"message": "Bad credentials"})
        return httpx.Response(
            200,
            json={"id": user.id, "login": user.login, "name": user.name, "avatar_url": None},
        )

    def _installations(self, request: httpx.Request) -> httpx.Response:
        user = self._caller(request)
        if user is None:
            return httpx.Response(401)
        items = [inst.as_json() for inst, _ in user.installations.values()]
        return httpx.Response(200, json={"total_count": len(items), "installations": items})

    def _repos(self, request: httpx.Request, inst_id: str) -> httpx.Response:
        user = self._caller(request)
        if user is None or int(inst_id) not in user.installations:
            return httpx.Response(404)
        repos = [r.as_json() for r in user.installations[int(inst_id)][1]]
        return httpx.Response(200, json={"total_count": len(repos), "repositories": repos})

    def _org_membership(self, request: httpx.Request, org: str) -> httpx.Response:
        user = self._caller(request)
        role = user.org_roles.get(org) if user else None
        if role is None:
            return httpx.Response(404)
        if role == "forbidden":
            return httpx.Response(403, json={"message": "Resource not accessible by integration"})
        return httpx.Response(200, json={"state": "active", "role": role})

    def _revoke(self, request: httpx.Request) -> httpx.Response:
        self.revoked.append(json.loads(request.content)["access_token"])
        return httpx.Response(204)


def start_login(client: TestClient) -> str:
    """GET /auth/github/login; returns the `state` GitHub would echo back."""
    response = client.get("/auth/github/login")
    assert response.status_code == 302, response.text
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def sign_in(client: TestClient, user: FakeUser) -> httpx.Response:
    """The whole browser dance: our login endpoint -> (GitHub consent) -> our callback."""
    state = start_login(client)
    return client.get("/auth/github/callback", params={"code": user.code, "state": state})
