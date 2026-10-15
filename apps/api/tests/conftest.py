import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    # _env_file=None so a developer's local .env can never leak into tests.
    return Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        web_origin="http://localhost:5173",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    # raise_server_exceptions=False: we want to assert on the 500 response the client would get.
    return TestClient(create_app(settings), raise_server_exceptions=False)
