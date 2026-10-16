"""Write the API's OpenAPI schema to a file (default: the web app's snapshot).

Run from apps/api:   uv run python -m scripts.dump_openapi [path]

The web app generates its TypeScript types from this snapshot (`npm run gen:api-types`), so
the Pydantic models stay the single source of truth. CI re-runs this and fails if the committed
snapshot differs. It needs no database: building the app is lazy, and settings are fixed
placeholders so a developer's .env cannot change the output.
"""

import json
import sys
from pathlib import Path

from app.config import Settings
from app.main import create_app

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "web" / "src" / "api" / "openapi.json"


def build_schema() -> dict[str, object]:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="postgresql+asyncpg://placeholder:placeholder@localhost:5432/placeholder",
        web_origin="http://localhost:5173",
        environment="test",
        jwt_secret="openapi-dump-placeholder-secret-0123456789",
        github_oauth_client_id="Iv1.placeholder",
        github_oauth_client_secret="placeholder-secret",
        github_webhook_secret="openapi-dump-placeholder-webhook-secret",
        llm_provider="fake",
    )
    return create_app(settings).openapi()


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys + trailing newline: stable output, so `git diff` only shows real changes.
    target.write_text(
        json.dumps(build_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
