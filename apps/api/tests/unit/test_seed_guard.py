import pytest

from app.config import Settings
from scripts.seed import UnsafeSeedTarget, assert_safe_target
from tests.app_utils import make_settings


def _settings(environment: str, host: str) -> Settings:
    return make_settings(
        database_url=f"postgresql+asyncpg://u:p@{host}:5432/codelens", environment=environment
    )


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_development_on_localhost_is_allowed(host: str) -> None:
    assert_safe_target(_settings("development", host))


@pytest.mark.parametrize("environment", ["production", "test"])
def test_other_environments_are_refused(environment: str) -> None:
    with pytest.raises(UnsafeSeedTarget, match="ENVIRONMENT"):
        assert_safe_target(_settings(environment, "localhost"))


def test_remote_host_is_refused_even_in_development() -> None:
    with pytest.raises(UnsafeSeedTarget, match="not localhost"):
        assert_safe_target(_settings("development", "db.abcd.supabase.co"))


def test_environment_defaults_to_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    settings = Settings(  # no `environment` argument on purpose
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@localhost:5432/codelens",
        web_origin="http://localhost:5173",
        jwt_secret="x" * 32,
        github_oauth_client_id="id",
        github_oauth_client_secret="secret-value",
    )
    assert settings.environment == "production"
    with pytest.raises(UnsafeSeedTarget):
        assert_safe_target(settings)
