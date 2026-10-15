from fastapi.testclient import TestClient

from app import __version__


def test_health_reports_status_version_and_unconfigured_database(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "database": "not_configured",
    }
