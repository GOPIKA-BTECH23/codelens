from fastapi.testclient import TestClient

from app.core.sentry import init_sentry
from app.main import create_app
from tests.app_utils import make_settings


def test_debug_sse_is_not_mounted_by_default() -> None:
    client = TestClient(create_app(make_settings()))

    assert client.get("/debug/sse").status_code == 404


def test_debug_sse_streams_events_when_enabled() -> None:
    client = TestClient(create_app(make_settings(debug_sse_enabled=True)))

    response = client.get("/debug/sse?count=3&interval=0.1")

    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.count("data: ") == 4  # 3 ticks + the final "done" event
    assert "event: done" in response.text


def test_sentry_is_off_without_a_dsn() -> None:
    assert init_sentry(make_settings()) is False
