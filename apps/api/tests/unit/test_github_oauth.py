"""GitHubOAuthClient edge cases (respx intercepts httpx; no network)."""

import httpx
import pytest
import respx

from app.services.github_oauth import GITHUB_API, GitHubOAuthClient, GitHubOAuthError
from tests.app_utils import make_settings

SETTINGS = make_settings()
TOKEN = "ghu_unit_test_token"


@pytest.fixture
async def gh() -> GitHubOAuthClient:  # type: ignore[misc]
    async with httpx.AsyncClient() as http:
        yield GitHubOAuthClient(http, SETTINGS)


async def test_token_exchange_error_reported_with_http_200(
    gh: GitHubOAuthClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post("https://github.com/login/oauth/access_token").respond(
        200, json={"error": "bad_verification_code", "error_description": "expired"}
    )
    with pytest.raises(GitHubOAuthError, match="bad_verification_code"):
        await gh.exchange_code("c", "v")


async def test_token_exchange_sends_secrets_in_the_body_not_the_url(
    gh: GitHubOAuthClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("https://github.com/login/oauth/access_token").respond(
        200, json={"access_token": TOKEN}
    )
    assert await gh.exchange_code("the-code", "the-verifier") == TOKEN

    request = route.calls.last.request
    assert request.url.query == b""
    assert b"client_secret=test-client-secret" in request.content
    assert request.headers["accept"] == "application/json"


async def test_installations_follow_pagination_links(
    gh: GitHubOAuthClient, respx_mock: respx.MockRouter
) -> None:
    page2 = f"{GITHUB_API}/user/installations?per_page=100&page=2"
    respx_mock.get(f"{GITHUB_API}/user/installations", params={"page": "2"}).respond(
        200,
        json={"installations": [{"id": 2, "account": {"id": 20, "login": "b", "type": "User"}}]},
    )
    respx_mock.get(f"{GITHUB_API}/user/installations").respond(
        200,
        json={"installations": [{"id": 1, "account": {"id": 10, "login": "a", "type": "User"}}]},
        headers={"Link": f'<{page2}>; rel="next"'},
    )

    installations = await gh.list_installations(TOKEN)

    assert [i.id for i in installations] == [1, 2]


async def test_pagination_link_to_another_host_is_refused(
    gh: GitHubOAuthClient, respx_mock: respx.MockRouter
) -> None:
    """Following it would send the user's token to that host."""
    respx_mock.get(f"{GITHUB_API}/user/installations").respond(
        200,
        json={"installations": []},
        headers={"Link": '<https://evil.example/steal>; rel="next"'},
    )
    with pytest.raises(GitHubOAuthError, match="outside"):
        await gh.list_installations(TOKEN)


async def test_api_requests_carry_bearer_token_and_api_version(
    gh: GitHubOAuthClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(f"{GITHUB_API}/user").respond(200, json={"id": 1, "login": "a"})
    await gh.get_user(TOKEN)
    headers = route.calls.last.request.headers
    assert headers["authorization"] == f"Bearer {TOKEN}"
    assert headers["x-github-api-version"] == "2026-03-10"


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, {"state": "active", "role": "admin"}, "admin"),
        (200, {"state": "active", "role": "member"}, "member"),
        (200, {"state": "pending", "role": "admin"}, None),  # invitation not accepted yet
        (403, {"message": "Resource not accessible by integration"}, None),
        (404, {"message": "Not Found"}, None),
    ],
)
async def test_org_role(
    gh: GitHubOAuthClient,
    respx_mock: respx.MockRouter,
    status: int,
    body: dict[str, str],
    expected: str | None,
) -> None:
    respx_mock.get(f"{GITHUB_API}/user/memberships/orgs/acme").respond(status, json=body)
    assert await gh.get_org_role(TOKEN, "acme") == expected


async def test_github_outage_becomes_a_login_error(
    gh: GitHubOAuthClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{GITHUB_API}/user").respond(502)
    with pytest.raises(GitHubOAuthError, match="502"):
        await gh.get_user(TOKEN)


async def test_revoke_uses_basic_auth_and_swallows_failures(
    gh: GitHubOAuthClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.delete(
        f"{GITHUB_API}/applications/{SETTINGS.github_oauth_client_id}/token"
    ).respond(422)

    await gh.revoke_token(TOKEN)  # does not raise

    assert route.calls.last.request.headers["authorization"].startswith("Basic ")
