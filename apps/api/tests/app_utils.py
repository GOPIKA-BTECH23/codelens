"""Building apps and "browsers" for tests.

The real browser reaches the API at WEB_ORIGIN/api/... and a proxy strips /api. Cookie paths
(/api/auth for the refresh token) only work if tests use the same URLs, so test clients
talk to the app through StripApiPrefix with base_url http://testserver/api.
"""

from typing import Any

from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import PUBLIC_API_PREFIX, Settings
from app.core.csrf import CSRF_HEADER, CSRF_HEADER_VALUE

WEB_ORIGIN = "http://localhost:5173"
TEST_JWT_SECRET = "test-only-jwt-secret-0123456789abcdef"
TEST_CLIENT_ID = "Iv1.testclientid"
TEST_CLIENT_SECRET = "test-client-secret"
TEST_WEBHOOK_SECRET = "test-only-webhook-secret-0123456789"


def make_settings(**overrides: Any) -> Settings:
    """Settings for tests. _env_file=None so a developer's local .env never leaks in."""
    values: dict[str, Any] = {
        "database_url": "postgresql+asyncpg://test:test@localhost:5432/test",
        "web_origin": WEB_ORIGIN,
        "environment": "test",
        "jwt_secret": TEST_JWT_SECRET,
        "github_oauth_client_id": TEST_CLIENT_ID,
        "github_oauth_client_secret": TEST_CLIENT_SECRET,
        "github_webhook_secret": TEST_WEBHOOK_SECRET,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class StripApiPrefix:
    """What the Vite proxy / Vercel rewrite do: /api/foo -> /foo."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith(f"{PUBLIC_API_PREFIX}/"):
            path = scope["path"][len(PUBLIC_API_PREFIX) :]
            scope = {**scope, "path": path, "raw_path": path.encode()}
        await self.app(scope, receive, send)


def browser(app: ASGIApp, *, csrf_header: bool = True) -> TestClient:
    """A TestClient that behaves like our web app in a browser: same /api URLs, a cookie
    jar, and (by default) the CSRF header our API client always sends. Use it as a
    context manager so the app's lifespan runs and its engine is disposed."""
    headers = {CSRF_HEADER: CSRF_HEADER_VALUE} if csrf_header else {}
    return TestClient(
        StripApiPrefix(app),
        base_url=f"http://testserver{PUBLIC_API_PREFIX}",
        headers=headers,
        follow_redirects=False,
        raise_server_exceptions=False,
    )


def replace_cookie(client: TestClient, name: str, value: str) -> None:
    """Overwrite a cookie the server set, keeping its domain and path (what a thief who
    copied a cookie would do)."""
    for cookie in list(client.cookies.jar):
        if cookie.name == name:
            client.cookies.jar.clear(cookie.domain, cookie.path, cookie.name)
            client.cookies.set(name, value, domain=cookie.domain, path=cookie.path)
            return
    raise AssertionError(f"cookie {name} is not set")


def cookie_value(client: TestClient, name: str) -> str | None:
    for cookie in client.cookies.jar:
        if cookie.name == name:
            return cookie.value
    return None
