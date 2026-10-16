"""App JWT, private-key loading and the installation-token cache (respx; no network)."""

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
import respx

from app.services.github_app import (
    GitHubApp,
    GitHubAppError,
    InstallationToken,
    create_app_jwt,
    load_private_key,
)
from tests.keys import app_private_key, app_private_key_pem

CLIENT_ID = "Iv23.testclientid"
INSTALLATION_ID = 4242
TOKEN_URL = f"https://api.github.com/app/installations/{INSTALLATION_ID}/access_tokens"
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def token_response(token: str, expires_at: datetime) -> httpx.Response:
    return httpx.Response(
        201,
        json={
            "token": token,
            "expires_at": expires_at.isoformat().replace("+00:00", "Z"),  # GitHub's format
            "permissions": {"pull_requests": "write"},
            "repository_selection": "selected",
        },
    )


# --- private key loading -------------------------------------------------------------------


def test_loads_pem_text_base64_pem_escaped_newlines_and_a_file(tmp_path: Path) -> None:
    pem = app_private_key_pem()
    key_file = tmp_path / "codelens.private-key.pem"
    key_file.write_text(pem)
    variants = [
        pem,
        base64.b64encode(pem.encode()).decode(),
        pem.replace("\n", "\\n"),  # how some dashboards store multi-line values
        str(key_file),
    ]
    expected = app_private_key().private_numbers()
    for value in variants:
        assert load_private_key(value).private_numbers() == expected


@pytest.mark.parametrize(
    "value",
    [
        "not a key",
        base64.b64encode(b"just some bytes").decode(),
        "-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----",
        "C:/definitely/not/here.pem",
    ],
)
def test_bad_keys_raise_value_error(value: str) -> None:
    with pytest.raises(ValueError):
        load_private_key(value)


# --- app JWT -------------------------------------------------------------------------------


def test_app_jwt_follows_githubs_rules() -> None:
    token = create_app_jwt(CLIENT_ID, app_private_key(), now=NOW)

    assert jwt.get_unverified_header(token)["alg"] == "RS256"
    claims = jwt.decode(
        token,
        app_private_key().public_key(),
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims["iss"] == CLIENT_ID
    assert claims["iat"] == int(NOW.timestamp()) - 60  # backdated for clock drift
    assert claims["exp"] - int(NOW.timestamp()) <= 600  # GitHub's 10-minute maximum
    assert claims["exp"] > int(NOW.timestamp())


# --- installation token cache --------------------------------------------------------------


@pytest.fixture
async def http() -> httpx.AsyncClient:  # type: ignore[misc]
    async with httpx.AsyncClient() as client:
        yield client


def make_app(http: httpx.AsyncClient, clock: FakeClock) -> GitHubApp:
    return GitHubApp(http, client_id=CLIENT_ID, private_key=app_private_key(), clock=clock)


async def test_token_is_minted_with_the_app_jwt_and_reused(
    http: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(TOKEN_URL).mock(
        return_value=token_response("ghs_first", NOW + timedelta(hours=1))
    )
    app = make_app(http, FakeClock(NOW))

    assert await app.installation_token(INSTALLATION_ID) == "ghs_first"
    assert await app.installation_token(INSTALLATION_ID) == "ghs_first"

    assert route.call_count == 1
    sent = route.calls.last.request
    bearer = sent.headers["authorization"].removeprefix("Bearer ")
    claims = jwt.decode(
        bearer, app_private_key().public_key(), algorithms=["RS256"], options={"verify_exp": False}
    )
    assert claims["iss"] == CLIENT_ID
    assert sent.headers["x-github-api-version"]


async def test_token_is_refreshed_shortly_before_it_expires(
    http: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(TOKEN_URL).mock(
        side_effect=[
            token_response("ghs_first", NOW + timedelta(hours=1)),
            token_response("ghs_second", NOW + timedelta(hours=2)),
        ]
    )
    clock = FakeClock(NOW)
    app = make_app(http, clock)
    assert await app.installation_token(INSTALLATION_ID) == "ghs_first"

    clock.now = NOW + timedelta(minutes=54)  # 6 minutes left: still outside the margin
    assert await app.installation_token(INSTALLATION_ID) == "ghs_first"

    clock.now = NOW + timedelta(minutes=56)  # 4 minutes left: inside the 5-minute margin
    assert await app.installation_token(INSTALLATION_ID) == "ghs_second"
    assert route.call_count == 2


async def test_concurrent_callers_share_one_token_request(
    http: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return token_response("ghs_shared", NOW + timedelta(hours=1))

    route = respx_mock.post(TOKEN_URL).mock(side_effect=slow)
    app = make_app(http, FakeClock(NOW))

    tokens = await asyncio.gather(*(app.installation_token(INSTALLATION_ID) for _ in range(5)))

    assert tokens == ["ghs_shared"] * 5
    assert route.call_count == 1


async def test_invalidate_forces_a_new_token(
    http: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(TOKEN_URL).mock(
        side_effect=[
            token_response("ghs_revoked", NOW + timedelta(hours=1)),
            token_response("ghs_new", NOW + timedelta(hours=1)),
        ]
    )
    app = make_app(http, FakeClock(NOW))
    await app.installation_token(INSTALLATION_ID)
    app.invalidate(INSTALLATION_ID)
    assert await app.installation_token(INSTALLATION_ID) == "ghs_new"


async def test_long_stateless_token_format_is_accepted(
    http: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    long_token = "ghs_12345_" + "x" * 600  # 2026 stateless format: no fixed length
    respx_mock.post(TOKEN_URL).mock(
        return_value=token_response(long_token, NOW + timedelta(hours=1))
    )
    assert await make_app(http, FakeClock(NOW)).installation_token(INSTALLATION_ID) == long_token


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(404, json={"message": "Not Found"}), "not found"),
        (httpx.Response(401, json={"message": "bad JWT"}), "rejected the app JWT"),
        (httpx.Response(500), "HTTP 500"),
        (httpx.Response(201, json={"expires_at": NOW.isoformat()}), "malformed"),
    ],
)
async def test_token_errors_are_reported_without_secrets(
    http: httpx.AsyncClient, respx_mock: respx.MockRouter, response: httpx.Response, message: str
) -> None:
    respx_mock.post(TOKEN_URL).mock(return_value=response)
    app = make_app(http, FakeClock(NOW))
    with pytest.raises(GitHubAppError, match=message) as exc_info:
        await app.installation_token(INSTALLATION_ID)
    assert "BEGIN" not in str(exc_info.value)


def test_token_never_appears_in_repr() -> None:
    token = InstallationToken(token="ghs_SECRET", expires_at=NOW)
    assert "ghs_SECRET" not in repr(token)
    assert "ghs_SECRET" not in str(token)
