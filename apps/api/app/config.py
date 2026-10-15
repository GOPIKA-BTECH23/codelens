"""Application settings. One class, validated at startup, so a bad config fails fast."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings.

    Only variables that M0 actually uses are required. Each later milestone adds its own
    variables here (and promotes them to required once the feature needs them), so a clean
    clone can boot before the GitHub App, LLM keys, etc. exist.
    """

    # Later files win: the repo-root .env (used when running outside Docker) is read after
    # an apps/api/.env, and real environment variables always beat both.
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        extra="ignore",  # .env.example documents variables that later milestones will use
    )

    # SecretStr keeps the connection string (it contains the password) out of logs and reprs.
    database_url: SecretStr
    web_origin: str
    api_origin: str = "http://localhost:8000"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator("database_url")
    @classmethod
    def _database_url_is_postgres(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql"):
            raise ValueError("must start with 'postgresql' (e.g. postgresql+asyncpg://...)")
        return value

    @field_validator("web_origin", "api_origin")
    @classmethod
    def _origin_is_clean(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return value.rstrip("/")  # origins are compared as exact strings later (CORS, redirects)


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process. Raises pydantic.ValidationError if config is invalid."""
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment
