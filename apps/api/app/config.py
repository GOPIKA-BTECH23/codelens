"""Application settings. One class, validated at startup, so a bad config fails fast."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.db.constants import EMBEDDING_DIM

# The browser reaches the API at WEB_ORIGIN + this prefix: the Vite proxy (dev) and the
# Vercel rewrite (production) strip it before forwarding. Cookie paths and the OAuth
# callback URL are what the BROWSER sees, so they include it. See ADR 0002.
PUBLIC_API_PREFIX = "/api"

# Placeholder values from .env.example that must never reach a running server.
_PLACEHOLDER_SECRETS = frozenset({"change-me", "changeme", "secret"})


class Settings(BaseSettings):
    """Environment-driven settings.

    Each milestone adds its own variables here (and makes them required once a feature
    needs them), so a clean clone fails at startup with a clear message instead of later
    at request time.
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
    # Defaults to "production" so a forgotten variable can never unlock dev-only tooling
    # (the seed script refuses to run unless this is "development") or drop cookie flags.
    environment: Literal["development", "test", "production"] = "production"
    # Not a free setting: it must equal the column size baked into the schema.
    embedding_dim: int = EMBEDDING_DIM

    # --- Sessions (M2) ---
    # Signs our access JWTs and the OAuth state cookie. Rotating it logs everyone out.
    jwt_secret: SecretStr
    jwt_access_ttl_minutes: int = Field(default=15, ge=1, le=60)
    jwt_refresh_ttl_days: int = Field(default=7, ge=1, le=30)

    # --- GitHub login (M2): the GitHub App's own OAuth client (decision 11) ---
    github_oauth_client_id: str = Field(min_length=1)
    github_oauth_client_secret: SecretStr

    # --- Abuse protection (M2) ---
    # Requests per minute per client IP on /auth/*.
    auth_rate_limit_per_minute: int = Field(default=20, ge=1)

    @field_validator("embedding_dim")
    @classmethod
    def _embedding_dim_matches_schema(cls, value: int) -> int:
        if value != EMBEDDING_DIM:
            raise ValueError(
                f"EMBEDDING_DIM={value} does not match the schema ({EMBEDDING_DIM}). "
                "Changing it needs a migration and a full re-index (see ADR 0001)."
            )
        return value

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

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_is_strong(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if secret.lower() in _PLACEHOLDER_SECRETS or len(secret) < 32:
            raise ValueError(
                "must be at least 32 random characters (generate one with: openssl rand -hex 32)"
            )
        return value

    @field_validator("github_oauth_client_secret")
    @classmethod
    def _client_secret_is_set(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("must be set (GitHub App settings -> Client secrets)")
        return value

    @property
    def cookie_secure(self) -> bool:
        """Secure cookies everywhere except local http development and tests.

        Keyed on "not development/test" rather than "is production", so an unset or
        mistyped ENVIRONMENT (which defaults to production) keeps the flag on.
        """
        return self.environment not in ("development", "test")

    @property
    def oauth_redirect_uri(self) -> str:
        """The callback URL GitHub sends the browser back to. Must match the GitHub App."""
        return f"{self.web_origin}{PUBLIC_API_PREFIX}/auth/github/callback"


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process. Raises pydantic.ValidationError if config is invalid."""
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment
