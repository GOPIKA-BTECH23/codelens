"""Application settings. One class, validated at startup, so a bad config fails fast."""

from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
        # Pydantic normally echoes the rejected value in the error (input_value='...'). For
        # settings that value is often a secret, and startup errors end up in deploy logs.
        hide_input_in_errors=True,
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

    # --- GitHub App (M3) ---
    # Shared secret GitHub uses to sign every webhook delivery (X-Hub-Signature-256).
    github_webhook_secret: SecretStr
    # The App's private key: PEM text, base64-encoded PEM, or a path to the .pem file. Only
    # the worker uses it (from M5, when it first calls GitHub); when set, it is parsed at
    # startup so a bad key fails here and not at the first review. The JWT issuer is
    # github_oauth_client_id.
    github_app_private_key: SecretStr | None = None
    # PR authors we never review (comma-separated logins in the environment).
    webhook_ignored_pr_authors: Annotated[frozenset[str], NoDecode] = frozenset(
        {"dependabot[bot]", "renovate[bot]"}
    )

    # --- Worker (M4) ---
    # true: run the job worker as a background task inside the API process (free-tier demos,
    # decision 3). false: run it as its own process, `python -m app.worker.main`.
    run_worker_in_api: bool = False
    # Reviews stop (runs are marked `skipped`) once today's estimated spend reaches this.
    daily_cost_cap_usd: Decimal = Field(default=Decimal("5"), ge=0)
    worker_concurrency: int = Field(default=2, ge=1, le=20)  # jobs handled at the same time
    worker_poll_seconds: float = Field(default=2.0, gt=0)  # idle sleep between empty polls
    # A `running` job older than this is presumed abandoned and re-queued. It must be longer
    # than the slowest handler (a big review), or healthy jobs would be run twice.
    job_visibility_timeout_seconds: int = Field(default=300, ge=30)
    # On SIGTERM, how long in-flight jobs get to finish before they are released.
    worker_shutdown_grace_seconds: float = Field(default=20.0, ge=0)

    # --- LLM (M5) ---
    # Which provider reviews PRs. `fake` is for tests and local runs without a key; the worker
    # refuses it in production. The matching API key is checked when the worker starts (the
    # API process alone never calls an LLM, so it does not need one).
    llm_provider: Literal["groq", "gemini", "fake"] = "groq"
    # Empty = the provider's default (groq: openai/gpt-oss-120b, gemini: gemini-3.8-flash).
    # Must have a price in app/ai/llm/pricing.py.
    llm_model: str | None = None
    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    # Client-side limit on LLM calls started per minute (decision 10). Keep it under your
    # plan's limit; free tiers are small.
    llm_requests_per_minute: int = Field(default=20, ge=1, le=1000)
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    llm_max_retries: int = Field(default=2, ge=0, le=5)  # the SDK's own retries per call

    # --- Review policy (M5) ---
    # Findings below this confidence are not posted (they are counted in the summary).
    review_confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    # At most this many inline comments per PR; the rest are counted in the summary.
    review_max_comments: int = Field(default=15, ge=1, le=50)
    # Extra paths to skip, comma-separated, added to the built-in noise list. Forms:
    # `dir/` (a directory anywhere), `*.ext` / `name` (file name), `path/*.py` (whole path).
    review_ignore_patterns: Annotated[tuple[str, ...], NoDecode] = ()
    # Files with more changed lines than this are skipped as too large to review well.
    review_max_file_changed_lines: int = Field(default=1000, ge=1)
    # PRs with nothing to review (docs, lockfiles, generated or binary files only): `skip`
    # marks the run skipped and posts nothing; `comment` posts a short summary.
    review_no_code_behavior: Literal["skip", "comment"] = "skip"
    # Estimated diff tokens per LLM call. Larger diffs are split into several calls.
    review_chunk_max_tokens: int = Field(default=12_000, ge=1_000, le=100_000)
    # LLM calls in flight at once for one review (the rate limiter still applies).
    review_llm_concurrency: int = Field(default=2, ge=1, le=8)

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

    @field_validator(
        "jwt_secret",
        "github_oauth_client_id",
        "github_oauth_client_secret",
        "github_webhook_secret",
        "groq_api_key",
        "gemini_api_key",
        mode="before",
    )
    @classmethod
    def _no_paste_artifacts(cls, value: object) -> object:
        """Catch copy-paste mistakes in credentials at startup instead of at the first login.

        A `<placeholder>` left around a pasted secret, quotes inside the value, or a stray
        space all produce a credential that looks set but is wrong (GitHub then answers
        `incorrect_client_credentials`). No exact length or format is enforced: providers
        change those. The message never includes the value.
        """
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not isinstance(text, str):
            return value
        problems = []
        if text != text.strip():
            problems.append("leading or trailing whitespace")
        if "<" in text or ">" in text:
            problems.append("angle brackets (a leftover <placeholder>?)")
        if '"' in text or "'" in text:
            problems.append("quote characters")
        if problems:
            raise ValueError(
                f"contains {' and '.join(problems)}; paste the bare value in .env (value not shown)"
            )
        return value

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

    @field_validator("github_webhook_secret")
    @classmethod
    def _webhook_secret_is_strong(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if secret.lower() in _PLACEHOLDER_SECRETS or len(secret) < 16:
            raise ValueError(
                "must be at least 16 random characters, the same value as in the GitHub App "
                "settings (generate one with: openssl rand -hex 32)"
            )
        return value

    @field_validator(
        "github_app_private_key", "groq_api_key", "gemini_api_key", "llm_model", mode="before"
    )
    @classmethod
    def _empty_means_unset(cls, value: object) -> object:
        # `GROQ_API_KEY=` in .env arrives as "". Pydantic runs "before" validators in reverse
        # order of definition, so this runs before the paste-artifact check above.
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(text, str) and not text.strip():
            return None
        return value

    @field_validator("github_app_private_key")
    @classmethod
    def _private_key_parses(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            # Imported here: config is imported by everything, the key loader needs crypto.
            from app.services.github_app import load_private_key

            load_private_key(value.get_secret_value())  # raises ValueError, never shows the key
        return value

    @field_validator("review_ignore_patterns", mode="before")
    @classmethod
    def _split_patterns(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(p.strip() for p in value.split(",") if p.strip())
        return value

    @field_validator("webhook_ignored_pr_authors", mode="before")
    @classmethod
    def _split_logins(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(login.strip() for login in value.split(",") if login.strip())
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
