import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.errors import NotFoundError
from app.main import create_app


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/_boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/_missing")
    async def missing() -> None:
        raise NotFoundError("Repository not found")

    @app.get("/_needs-int")
    async def needs_int(n: int) -> dict[str, int]:
        return {"n": n}

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_app_error_uses_standard_shape(client: TestClient) -> None:
    response = client.get("/_missing")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Repository not found"}}


def test_unknown_route_uses_standard_shape(client: TestClient) -> None:
    response = client.get("/nope")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_404"


def test_validation_error_lists_fields_without_echoing_input(client: TestClient) -> None:
    response = client.get("/_needs-int", params={"n": "not-a-number"})

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"][0]["loc"] == ["query", "n"]
    assert "not-a-number" not in response.text


def test_unexpected_exception_hides_internals_but_keeps_request_id(client: TestClient) -> None:
    response = client.get("/_boom")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error"}
    }
    assert "secret internal detail" not in response.text
    assert response.headers["X-Request-ID"]


def test_request_id_is_generated_and_returned(client: TestClient) -> None:
    response = client.get("/health")

    assert len(response.headers["X-Request-ID"]) == 32


def test_valid_client_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"


def test_unsafe_client_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "bad id with spaces"})

    assert response.headers["X-Request-ID"] != "bad id with spaces"
    assert len(response.headers["X-Request-ID"]) == 32


def test_access_log_line_has_path_and_status(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.access"):
        client.get("/health", headers={"X-Request-ID": "trace-me"})

    record = next(r for r in caplog.records if r.name == "app.access")
    assert record.__dict__["path"] == "/health"
    assert record.__dict__["status"] == 200
