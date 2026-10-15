"""CSRF, CORS, security headers, rate limiting, and "no tokens in the logs"."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.rate_limit import PostgresRateLimiter
from app.core.security import ACCESS_COOKIE, REFRESH_COOKIE
from app.db.models import RateLimitCounter
from tests.app_utils import WEB_ORIGIN, cookie_value
from tests.conftest import OpenBrowser
from tests.fake_github import FakeGitHub, FakeInstallation, FakeRepo, FakeUser, sign_in

# --- CSRF ----------------------------------------------------------------------------------


def test_state_changing_request_without_the_csrf_header_is_403(open_browser: OpenBrowser) -> None:
    response = open_browser(csrf_header=False).post("/auth/logout")

    assert response.status_code == 403
    assert response.json() == {
        "error": {"code": "csrf_failed", "message": "Cross-site request rejected"}
    }
    assert "X-Request-ID" in response.headers


def test_wrong_csrf_header_value_is_403(open_browser: OpenBrowser) -> None:
    client = open_browser(csrf_header=False)
    response = client.post("/auth/refresh", headers={"X-Requested-With": "XMLHttpRequest"})
    assert response.status_code == 403


def test_foreign_origin_is_403_even_with_the_header(open_browser: OpenBrowser) -> None:
    response = open_browser().post("/auth/logout", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_our_own_origin_with_the_header_passes(open_browser: OpenBrowser) -> None:
    response = open_browser().post("/auth/logout", headers={"Origin": WEB_ORIGIN})
    assert response.status_code == 204


def test_csrf_failure_does_not_touch_the_session(
    open_browser: OpenBrowser, github: FakeGitHub
) -> None:
    user = github.add(FakeUser(id=1, login="alice"))
    client = open_browser()
    sign_in(client, user)

    del client.headers["X-Requested-With"]  # a forged cross-site POST has no such header
    assert client.post("/auth/logout").status_code == 403
    assert cookie_value(client, REFRESH_COOKIE) is not None
    assert client.get("/me").status_code == 200  # GETs never need the header


# --- CORS ----------------------------------------------------------------------------------


def _preflight(open_browser: OpenBrowser, origin: str) -> object:
    return open_browser().options(
        "/auth/refresh",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-requested-with",
        },
    )


def test_cors_preflight_from_the_web_origin_is_allowed(open_browser: OpenBrowser) -> None:
    response = _preflight(open_browser, WEB_ORIGIN)
    assert response.status_code == 200  # type: ignore[attr-defined]
    headers = response.headers  # type: ignore[attr-defined]
    assert headers["access-control-allow-origin"] == WEB_ORIGIN
    assert headers["access-control-allow-credentials"] == "true"


def test_cors_preflight_from_another_origin_is_refused(open_browser: OpenBrowser) -> None:
    response = _preflight(open_browser, "https://evil.example")
    assert response.status_code == 400  # type: ignore[attr-defined]
    assert "access-control-allow-origin" not in response.headers  # type: ignore[attr-defined]


# --- security headers ----------------------------------------------------------------------


def test_security_headers_on_api_responses(open_browser: OpenBrowser) -> None:
    headers = open_browser().get("/me").headers  # also on errors (401 here)

    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"
    assert headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert "strict-transport-security" not in headers  # http in tests: no HSTS


def test_hsts_in_production(open_browser: OpenBrowser, api_settings: Settings) -> None:
    prod = api_settings.model_copy(update={"environment": "production"})
    headers = open_browser(settings=prod).get("/health").headers
    assert headers["strict-transport-security"].startswith("max-age=")


def test_docs_page_still_works_without_the_strict_csp(open_browser: OpenBrowser) -> None:
    response = open_browser().get("/docs")
    assert response.status_code == 200
    assert "content-security-policy" not in response.headers


# --- rate limiting -------------------------------------------------------------------------


async def test_auth_endpoints_are_rate_limited_per_ip(
    open_browser: OpenBrowser, api_settings: Settings
) -> None:
    client = open_browser(
        settings=api_settings.model_copy(update={"auth_rate_limit_per_minute": 3})
    )

    statuses = [client.get("/auth/github/login").status_code for _ in range(4)]

    assert statuses == [302, 302, 302, 429]
    response = client.post("/auth/refresh")  # same IP, same bucket, any /auth route
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(response.headers["retry-after"]) <= 60
    assert client.get("/health").status_code == 200  # non-auth routes are not limited


async def test_rate_limiter_windows_reset_and_stale_rows_are_purged(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    limiter = PostgresRateLimiter(session_factory)
    t0 = datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC)

    results = [await limiter.hit("k", limit=2, window_seconds=60, now=t0) for _ in range(3)]
    assert [r.allowed for r in results] == [True, True, False]
    assert results[2].retry_after_seconds == 60

    later = t0 + timedelta(seconds=61)
    assert (await limiter.hit("k", limit=2, window_seconds=60, now=later)).allowed

    async with session_factory() as s, s.begin():
        await PostgresRateLimiter.purge_stale(s, later + timedelta(hours=2))
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(RateLimitCounter)) == 0


# --- secrets never reach the logs -----------------------------------------------------------


@pytest.mark.parametrize("log_level", ["INFO", "DEBUG"])
def test_tokens_never_appear_in_logs(
    open_browser: OpenBrowser,
    github: FakeGitHub,
    api_settings: Settings,
    capsys: pytest.CaptureFixture[str],
    log_level: str,
) -> None:
    user = github.add(
        FakeUser(id=1, login="alice").grant(
            FakeInstallation(id=100, account_login="alice", account_id=1), FakeRepo(500, "a/b")
        )
    )
    client = open_browser(settings=api_settings.model_copy(update={"log_level": log_level}))
    sign_in(client, user)
    seen = {
        "access": cookie_value(client, ACCESS_COOKIE),
        "refresh": cookie_value(client, REFRESH_COOKIE),
    }
    client.post("/auth/refresh")
    seen["access2"] = cookie_value(client, ACCESS_COOKIE)
    seen["refresh2"] = cookie_value(client, REFRESH_COOKIE)
    client.get("/me")
    client.post("/auth/logout")

    logs = capsys.readouterr().out
    assert "login succeeded" in logs  # we really captured the app's log output
    for name, secret in {
        **seen,
        "github user token": user.token,
        "jwt secret": api_settings.jwt_secret.get_secret_value(),
        "client secret": api_settings.github_oauth_client_secret.get_secret_value(),
    }.items():
        assert secret and secret not in logs, f"{name} leaked into the logs"
    assert "ghu_" not in logs
