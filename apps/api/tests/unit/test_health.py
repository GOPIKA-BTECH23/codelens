from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import __version__
from app.config import Settings
from app.main import create_app


def _unreachable_settings(settings: Settings) -> Settings:
    # Port 1 refuses connections immediately, so these tests need no database.
    return settings.model_copy(
        update={"database_url": SecretStr("postgresql+asyncpg://u:p@127.0.0.1:1/none")}
    )


def test_health_is_200_with_database_unavailable_when_postgres_is_down(settings: Settings) -> None:
    with TestClient(create_app(_unreachable_settings(settings))) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "database": "unavailable",
        "revision": None,
    }


def test_ready_is_503_with_the_standard_error_shape_when_postgres_is_down(
    settings: Settings,
) -> None:
    with TestClient(create_app(_unreachable_settings(settings))) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "service_unavailable", "message": "Database unavailable"}
    }
