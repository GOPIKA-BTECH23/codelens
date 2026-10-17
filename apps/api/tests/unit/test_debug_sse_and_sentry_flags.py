from fastapi.testclient import TestClient

from app.core.sentry import init_sentry
from app.main import create_app
from tests.app_utils import make_settings


def test_debug_sse_endpoint_is_gone() -> None:
    client = TestClient(create_app(make_settings()))

    assert client.get("/debug/sse").status_code == 404


def test_sentry_is_off_without_a_dsn() -> None:
    assert init_sentry(make_settings()) is False


def test_health_reports_the_deployed_commit() -> None:
    client = TestClient(create_app(make_settings(RENDER_GIT_COMMIT="abc123")))

    # The database is unreachable in this unit test; /health still answers (liveness).
    body = client.get("/health").json()

    assert body["commit"] == "abc123"
    assert body["database"] == "unavailable"
