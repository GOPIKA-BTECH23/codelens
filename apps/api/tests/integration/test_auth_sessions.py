"""Access tokens, refresh rotation, reuse detection and logout."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.security import ACCESS_COOKIE, REFRESH_COOKIE, create_access_token
from app.db.models import RefreshToken, User
from tests.app_utils import cookie_value, replace_cookie
from tests.conftest import OpenBrowser
from tests.fake_github import FakeGitHub, FakeInstallation, FakeRepo, FakeUser, sign_in


def _signed_in(open_browser: OpenBrowser, github: FakeGitHub) -> TestClient:
    user = github.add(
        FakeUser(id=1, login="alice").grant(
            FakeInstallation(id=100, account_login="alice", account_id=1), FakeRepo(500, "a/b")
        )
    )
    client = open_browser()
    assert sign_in(client, user).status_code == 302
    return client


async def _tokens(session: AsyncSession) -> list[RefreshToken]:
    return list(
        (await session.scalars(select(RefreshToken).order_by(RefreshToken.created_at))).all()
    )


# --- access tokens -------------------------------------------------------------------------


def test_me_without_a_session_is_401(open_browser: OpenBrowser) -> None:
    response = open_browser().get("/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_expired_access_token_is_rejected(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession, api_settings: Settings
) -> None:
    client = _signed_in(open_browser, github)
    user = (await session.scalars(select(User))).one()
    long_ago = datetime.now(UTC) - timedelta(minutes=api_settings.jwt_access_ttl_minutes + 1)
    replace_cookie(client, ACCESS_COOKIE, create_access_token(user.id, api_settings, now=long_ago))

    response = client.get("/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_expired"


def test_garbage_access_token_is_rejected(open_browser: OpenBrowser, github: FakeGitHub) -> None:
    client = _signed_in(open_browser, github)
    replace_cookie(client, ACCESS_COOKIE, "not.a.jwt")

    response = client.get("/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_access_token_of_a_deleted_user_is_rejected(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    client = _signed_in(open_browser, github)
    await session.delete((await session.scalars(select(User))).one())
    await session.commit()

    assert client.get("/me").status_code == 401


# --- refresh rotation ----------------------------------------------------------------------


async def test_refresh_rotates_the_token_and_keeps_the_family_and_expiry(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    client = _signed_in(open_browser, github)
    old_refresh = cookie_value(client, REFRESH_COOKIE)

    response = client.post("/auth/refresh")

    assert response.status_code == 204
    assert cookie_value(client, REFRESH_COOKIE) not in (None, old_refresh)
    # A fresh access cookie is set. (Its value may equal the old one: JWTs are
    # deterministic, and two minted in the same second for the same user are identical.)
    set_cookies = response.headers.get_list("set-cookie")
    assert any(c.startswith(f"{ACCESS_COOKIE}=") for c in set_cookies)
    first, second = await _tokens(session)
    assert first.rotated_at is not None and first.revoked_at is None
    assert second.rotated_at is None and second.revoked_at is None
    assert second.family_id == first.family_id
    # Refreshing never extends the session beyond its original 7 days (decision 14).
    assert second.expires_at == first.expires_at
    assert client.get("/me").status_code == 200


async def test_refresh_token_reuse_revokes_the_whole_family(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    client = _signed_in(open_browser, github)
    stolen = cookie_value(client, REFRESH_COOKIE)
    assert client.post("/auth/refresh").status_code == 204  # the legitimate user rotates
    current = cookie_value(client, REFRESH_COOKIE)

    # The thief presents the old (already rotated) token.
    thief = open_browser()
    thief.cookies.set(REFRESH_COOKIE, stolen, domain="testserver.local", path="/api/auth")  # type: ignore[arg-type]
    response = thief.post("/auth/refresh")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_refresh_token"
    # Revocation was COMMITTED although the request failed...
    assert all(t.revoked_at is not None for t in await _tokens(session))
    # ...so the legitimate user's newest token is dead too: both must sign in again.
    replace_cookie(client, REFRESH_COOKIE, current)  # type: ignore[arg-type]
    assert client.post("/auth/refresh").status_code == 401


async def test_expired_refresh_token_is_rejected(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    client = _signed_in(open_browser, github)
    await session.execute(
        update(RefreshToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()

    response = client.post("/auth/refresh")

    assert response.status_code == 401
    assert "expired" in response.json()["error"]["message"]


def test_refresh_without_a_cookie_is_401(open_browser: OpenBrowser) -> None:
    assert open_browser().post("/auth/refresh").status_code == 401


def test_failed_refresh_clears_the_session_cookies(
    open_browser: OpenBrowser, github: FakeGitHub
) -> None:
    client = _signed_in(open_browser, github)
    replace_cookie(client, REFRESH_COOKIE, "unknown-token")

    response = client.post("/auth/refresh")

    assert response.status_code == 401
    assert cookie_value(client, REFRESH_COOKIE) is None
    assert cookie_value(client, ACCESS_COOKIE) is None


# --- logout --------------------------------------------------------------------------------


async def test_logout_revokes_the_session_and_clears_cookies(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    client = _signed_in(open_browser, github)
    refresh = cookie_value(client, REFRESH_COOKIE)

    response = client.post("/auth/logout")

    assert response.status_code == 204
    assert cookie_value(client, ACCESS_COOKIE) is None
    assert cookie_value(client, REFRESH_COOKIE) is None
    assert all(t.revoked_at is not None for t in await _tokens(session))
    assert client.get("/me").status_code == 401
    # Even a copy of the refresh token made before logout is useless now.
    client.cookies.set(REFRESH_COOKIE, refresh, domain="testserver.local", path="/api/auth")  # type: ignore[arg-type]
    assert client.post("/auth/refresh").status_code == 401


def test_logout_without_a_session_still_succeeds(open_browser: OpenBrowser) -> None:
    assert open_browser().post("/auth/logout").status_code == 204
