"""GitHub login: /auth/github/login and /auth/github/callback against a mocked GitHub."""

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import ACCESS_COOKIE, OAUTH_COOKIE, REFRESH_COOKIE
from app.db.models import (
    Installation,
    InstallationMember,
    RefreshToken,
    Repository,
    RepositoryAccess,
    User,
)
from tests.app_utils import TEST_CLIENT_ID, WEB_ORIGIN, cookie_value, replace_cookie
from tests.conftest import OpenBrowser
from tests.fake_github import (
    FakeGitHub,
    FakeInstallation,
    FakeRepo,
    FakeUser,
    sign_in,
    start_login,
)

LOGIN_ERROR = f"{WEB_ORIGIN}/login?error="


def _alice(github: FakeGitHub) -> FakeUser:
    personal = FakeInstallation(id=100, account_login="alice", account_id=1)
    return github.add(
        FakeUser(id=1, login="alice", name="Alice A").grant(
            personal, FakeRepo(500, "alice/app"), FakeRepo(501, "alice/lib", private=True)
        )
    )


def _set_cookie_headers(response: object) -> list[str]:
    return response.headers.get_list("set-cookie")  # type: ignore[attr-defined,no-any-return]


# --- /auth/github/login --------------------------------------------------------------------


def test_login_redirects_to_github_with_state_and_pkce(open_browser: OpenBrowser) -> None:
    client = open_browser()
    response = client.get("/auth/github/login")

    assert response.status_code == 302
    url = urlparse(response.headers["location"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://github.com/login/oauth/authorize"
    assert query["client_id"] == TEST_CLIENT_ID
    assert query["redirect_uri"] == f"{WEB_ORIGIN}/api/auth/github/callback"
    assert query["code_challenge_method"] == "S256"
    assert len(query["state"]) >= 43 and len(query["code_challenge"]) == 43

    (cookie,) = _set_cookie_headers(response)
    assert cookie.startswith(f"{OAUTH_COOKIE}=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Path=/api/auth/github" in cookie and "Max-Age=600" in cookie


def test_every_login_gets_a_fresh_state(open_browser: OpenBrowser) -> None:
    client = open_browser()
    assert start_login(client) != start_login(client)


# --- callback: rejections ------------------------------------------------------------------


def test_state_mismatch_is_rejected_before_any_github_call(
    open_browser: OpenBrowser, github: FakeGitHub
) -> None:
    user = _alice(github)
    client = open_browser()
    start_login(client)

    response = client.get(
        "/auth/github/callback", params={"code": user.code, "state": "attacker-chosen-state"}
    )

    assert response.status_code == 302
    assert response.headers["location"] == f"{LOGIN_ERROR}invalid_state"
    assert not github.token_route.called  # the code was never exchanged
    assert cookie_value(client, ACCESS_COOKIE) is None
    assert cookie_value(client, REFRESH_COOKIE) is None
    assert cookie_value(client, OAUTH_COOKIE) is None  # the attempt is used up


def test_callback_without_the_state_cookie_is_rejected(
    open_browser: OpenBrowser, github: FakeGitHub
) -> None:
    """Login CSRF: an attacker sends the victim a callback link from THEIR login attempt.
    The victim's browser has no matching cookie, so nothing happens."""
    user = _alice(github)
    attacker = open_browser()
    state = start_login(attacker)
    victim = open_browser()

    response = victim.get("/auth/github/callback", params={"code": user.code, "state": state})

    assert response.headers["location"] == f"{LOGIN_ERROR}invalid_state"
    assert not github.token_route.called


def test_tampered_state_cookie_is_rejected(open_browser: OpenBrowser, github: FakeGitHub) -> None:
    user = _alice(github)
    client = open_browser()
    state = start_login(client)
    replace_cookie(client, OAUTH_COOKIE, cookie_value(client, OAUTH_COOKIE)[:-3] + "xyz")  # type: ignore[index]

    response = client.get("/auth/github/callback", params={"code": user.code, "state": state})

    assert response.headers["location"] == f"{LOGIN_ERROR}invalid_state"
    assert not github.token_route.called


def test_user_cancelling_on_github_returns_to_login(
    open_browser: OpenBrowser, github: FakeGitHub
) -> None:
    client = open_browser()
    state = start_login(client)

    response = client.get(
        "/auth/github/callback", params={"error": "access_denied", "state": state}
    )

    assert response.headers["location"] == f"{LOGIN_ERROR}access_denied"
    assert not github.token_route.called


async def test_github_rejecting_the_code_creates_no_user(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    client = open_browser()
    state = start_login(client)

    response = client.get("/auth/github/callback", params={"code": "expired-code", "state": state})

    assert response.headers["location"] == f"{LOGIN_ERROR}github_error"
    assert cookie_value(client, ACCESS_COOKIE) is None
    assert (await session.scalars(select(User))).all() == []


# --- callback: success ---------------------------------------------------------------------


async def test_successful_login_syncs_github_and_starts_a_session(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    user = _alice(github)
    client = open_browser()

    response = sign_in(client, user)

    assert response.status_code == 302
    assert response.headers["location"] == f"{WEB_ORIGIN}/dashboard"

    # Cookies: httpOnly, SameSite=Lax, refresh token scoped to the auth endpoints.
    cookies = {c.split("=", 1)[0]: c for c in _set_cookie_headers(response)}
    assert "HttpOnly" in cookies[ACCESS_COOKIE] and "SameSite=lax" in cookies[ACCESS_COOKIE]
    assert "Path=/;" in cookies[ACCESS_COOKIE] + ";"
    assert "HttpOnly" in cookies[REFRESH_COOKIE] and "Path=/api/auth" in cookies[REFRESH_COOKIE]
    assert "Max-Age=0" in cookies[OAUTH_COOKIE]  # state cookie deleted

    # PKCE: the verifier we sent hashes to the challenge we published.
    (exchange,) = github.exchanges
    assert exchange["code"] == user.code
    assert exchange["redirect_uri"] == f"{WEB_ORIGIN}/api/auth/github/callback"
    assert len(exchange["code_verifier"]) >= 43

    # The GitHub user token was revoked once we were done with it.
    assert github.revoked == [user.token]

    # Database rows.
    db_user = (await session.scalars(select(User))).one()
    assert (db_user.github_id, db_user.login, db_user.name) == (1, "alice", "Alice A")
    assert db_user.last_login_at is not None
    installation = (await session.scalars(select(Installation))).one()
    assert (installation.github_installation_id, installation.account_type) == (100, "user")
    member = (await session.scalars(select(InstallationMember))).one()
    assert member.role == "owner"
    repos = {r.full_name: r for r in await session.scalars(select(Repository))}
    assert set(repos) == {"alice/app", "alice/lib"} and repos["alice/lib"].is_private
    assert len((await session.scalars(select(RepositoryAccess))).all()) == 2
    refresh = (await session.scalars(select(RefreshToken))).one()
    assert refresh.token_hash != cookie_value(client, REFRESH_COOKIE)  # stored hashed

    # And the session works.
    me = client.get("/me")
    assert me.status_code == 200
    assert me.json()["user"]["login"] == "alice"
    assert me.json()["installations"][0]["role"] == "owner"


def test_pkce_verifier_matches_the_published_challenge(
    open_browser: OpenBrowser, github: FakeGitHub
) -> None:
    user = _alice(github)
    client = open_browser()
    login = client.get("/auth/github/login")
    query = parse_qs(urlparse(login.headers["location"]).query)
    client.get("/auth/github/callback", params={"code": user.code, "state": query["state"][0]})

    verifier = github.exchanges[0]["code_verifier"]
    digest = hashlib.sha256(verifier.encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == query["code_challenge"][0]


async def test_roles_are_mapped_from_github(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    acme = FakeInstallation(
        id=200, account_login="acme", account_id=50, account_type="Organization"
    )
    globex = FakeInstallation(
        id=201, account_login="globex", account_id=51, account_type="Organization"
    )
    bobs = FakeInstallation(id=202, account_login="bob", account_id=2)
    user = github.add(
        FakeUser(id=1, login="alice", org_roles={"acme": "admin", "globex": "forbidden"})
    )
    user.grant(acme, FakeRepo(600, "acme/api"))
    user.grant(globex, FakeRepo(601, "globex/web"))  # App lacks Members: read here
    user.grant(bobs, FakeRepo(602, "bob/tool"))  # collaborator on Bob's personal repo

    sign_in(open_browser(), user)

    rows = await session.execute(
        select(Installation.account_login, InstallationMember.role).join(
            InstallationMember, InstallationMember.installation_id == Installation.id
        )
    )
    assert dict(rows.all()) == {"acme": "admin", "globex": "member", "bob": "member"}


async def test_next_login_removes_access_that_github_no_longer_grants(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    acme = FakeInstallation(
        id=200, account_login="acme", account_id=50, account_type="Organization"
    )
    user = _alice(github).grant(acme, FakeRepo(600, "acme/api"))
    sign_in(open_browser(), user)

    # On GitHub: Alice leaves acme and loses access to alice/lib... (not realistic for her own
    # repo, but it is what "the repo list shrank" looks like to us).
    del user.installations[200]
    user.installations[100][1].pop()  # alice/lib
    sign_in(open_browser(), user)

    memberships = (await session.scalars(select(InstallationMember))).all()
    assert len(memberships) == 1
    accessible = await session.scalars(
        select(Repository.full_name).join(
            RepositoryAccess, RepositoryAccess.repository_id == Repository.id
        )
    )
    assert accessible.all() == ["alice/app"]
    # The repository and installation rows themselves are kept (other users may need them).
    assert len((await session.scalars(select(Repository))).all()) == 3
