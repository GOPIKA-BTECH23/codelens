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
    "GITHUB_WEBHOOK_SECRET",
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


CREDENTIAL_FIELDS = [
    "jwt_secret",
    "github_oauth_client_id",
    "github_oauth_client_secret",
    "github_webhook_secret",
]
# Each bad value embeds this marker, so the test can prove the value is never echoed.
MARKER = "Zq9SentinelValue4fd0c1b2a3e4f5a6b7c8d9e0"


@pytest.mark.parametrize("field", CREDENTIAL_FIELDS)
@pytest.mark.parametrize(
    ("bad_value", "reason"),
    [
        (f"<{MARKER}>", "angle brackets"),
        (f"{MARKER}>", "angle brackets"),
        (f'"{MARKER}"', "quote characters"),
        (f"{MARKER}'", "quote characters"),
        (f" {MARKER}", "leading or trailing whitespace"),
        (f"{MARKER}\t", "leading or trailing whitespace"),
        (f"{MARKER}\r", "leading or trailing whitespace"),  # CRLF .env written on Windows
    ],
)
def test_credentials_with_paste_artifacts_are_rejected_without_echoing_them(
    field: str, bad_value: str, reason: str
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(**{field: bad_value})

    message = str(exc_info.value)
    assert field in message
    assert reason in message
    assert MARKER not in message  # neither the value nor any part of it


@pytest.mark.parametrize("field", CREDENTIAL_FIELDS)
def test_plain_credentials_of_any_length_and_alphabet_are_accepted(field: str) -> None:
    # No exact length or hex format is enforced: providers change those.
    value = "Iv23-Mixed_Case.value~0123456789+/=ABCDEFG"
    settings = make_settings(**{field: value})
    stored = getattr(settings, field)
    assert (stored if isinstance(stored, str) else stored.get_secret_value()) == value


def test_no_validation_error_ever_prints_the_rejected_value() -> None:
    """hide_input_in_errors: covers every field, including the existing checks."""
    with pytest.raises(ValidationError) as exc_info:
        make_settings(
            jwt_secret=f"short{MARKER[:8]}",
            database_url=f"mysql://user:{MARKER}@host/db",
        )
    assert MARKER[:8] not in str(exc_info.value)
    assert MARKER not in str(exc_info.value)


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
        github_webhook_secret="w" * 32,
    )
    assert s.environment == "production"
    assert s.cookie_secure is True


def test_oauth_redirect_uri_goes_through_the_web_origin_proxy() -> None:
    s = make_settings(web_origin="https://codelens.example")
    assert s.oauth_redirect_uri == "https://codelens.example/api/auth/github/callback"


@pytest.mark.parametrize("secret", ["change-me", "too-short-value"])
def test_weak_or_placeholder_webhook_secret_is_rejected(secret: str) -> None:
    with pytest.raises(ValidationError, match="github_webhook_secret"):
        make_settings(github_webhook_secret=secret)


def test_private_key_is_optional_and_empty_means_unset() -> None:
    assert make_settings().github_app_private_key is None
    assert make_settings(github_app_private_key="  ").github_app_private_key is None


def test_unparseable_private_key_fails_at_startup_without_echoing_it() -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(github_app_private_key=f"-----BEGIN RSA PRIVATE KEY-----{MARKER}")
    assert "github_app_private_key" in str(exc_info.value)
    assert MARKER not in str(exc_info.value)


def test_ignored_pr_authors_parse_from_a_comma_separated_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEBHOOK_IGNORED_PR_AUTHORS", " dependabot[bot], my-bot[bot] ,,")
    settings = make_settings()
    assert settings.webhook_ignored_pr_authors == {"dependabot[bot]", "my-bot[bot]"}


def test_ignored_pr_authors_default() -> None:
    assert make_settings().webhook_ignored_pr_authors == {"dependabot[bot]", "renovate[bot]"}
