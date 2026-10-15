import pytest
from pydantic import ValidationError

from app.config import Settings
from tests.app_utils import make_settings

REQUIRED = {
    "DATABASE_URL",
    "WEB_ORIGIN",
    "JWT_SECRET",
    "GITHUB_OAUTH_CLIENT_ID",
    "GITHUB_OAUTH_CLIENT_SECRET",
}


def test_missing_required_variables_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    missing = {str(err["loc"][0]).upper() for err in exc_info.value.errors()}
    assert missing == REQUIRED


def test_secrets_are_hidden_from_repr() -> None:
    s = make_settings()
    text = repr(s)
    assert "test:test" not in text
    assert s.jwt_secret.get_secret_value() not in text
    assert s.github_oauth_client_secret.get_secret_value() not in text


def test_origin_trailing_slash_is_stripped() -> None:
    assert make_settings(web_origin="http://localhost:5173/").web_origin == "http://localhost:5173"


def test_rejects_non_postgres_database_url() -> None:
    with pytest.raises(ValidationError):
        make_settings(database_url="mysql://u:p@h/db")


@pytest.mark.parametrize("secret", ["change-me", "short", "x" * 31])
def test_weak_or_placeholder_jwt_secret_is_rejected(secret: str) -> None:
    with pytest.raises(ValidationError, match="jwt_secret"):
        make_settings(jwt_secret=secret)


def test_empty_client_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="github_oauth_client_secret"):
        make_settings(github_oauth_client_secret="")


@pytest.mark.parametrize(
    ("environment", "secure"),
    [("development", False), ("test", False), ("production", True)],
)
def test_cookies_are_secure_unless_local(environment: str, secure: bool) -> None:
    assert make_settings(environment=environment).cookie_secure is secure


def test_unset_environment_defaults_to_secure_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    s = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        web_origin="https://codelens.example",
        jwt_secret="x" * 32,
        github_oauth_client_id="id",
        github_oauth_client_secret="secret-value",
    )
    assert s.environment == "production"
    assert s.cookie_secure is True


def test_oauth_redirect_uri_goes_through_the_web_origin_proxy() -> None:
    s = make_settings(web_origin="https://codelens.example")
    assert s.oauth_redirect_uri == "https://codelens.example/api/auth/github/callback"
