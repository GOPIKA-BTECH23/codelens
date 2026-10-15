import pytest

from app.config import Settings
from scripts.seed import UnsafeSeedTarget, assert_safe_target


def _settings(environment: str, host: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"postgresql+asyncpg://u:p@{host}:5432/codelens",
        web_origin="http://localhost:5173",
        environment=environment,  # type: ignore[arg-type]
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


def test_environment_defaults_to_production(settings: Settings) -> None:
    assert settings.environment == "production"
