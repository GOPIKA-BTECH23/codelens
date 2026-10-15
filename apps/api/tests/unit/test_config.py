import pytest
from pydantic import ValidationError

from app.config import Settings


def test_missing_required_variables_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("WEB_ORIGIN", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    missing = {err["loc"][0] for err in exc_info.value.errors()}
    assert missing == {"database_url", "web_origin"}


def test_database_url_is_a_secret(settings: Settings) -> None:
    assert "test:test" not in repr(settings)


def test_origin_trailing_slash_is_stripped() -> None:
    s = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        web_origin="http://localhost:5173/",
    )
    assert s.web_origin == "http://localhost:5173"


def test_rejects_non_postgres_database_url() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="mysql://u:p@h/db", web_origin="http://x")
