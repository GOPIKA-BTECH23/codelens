"""The committed OpenAPI snapshot (source of the web app's generated types) matches the API."""

import json

from scripts.dump_openapi import DEFAULT_PATH, build_schema


def test_committed_openapi_snapshot_is_current() -> None:
    committed = json.loads(DEFAULT_PATH.read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(build_schema())), (
        "apps/web/src/api/openapi.json is stale. Run: cd apps/api && "
        "uv run python -m scripts.dump_openapi && cd ../web && npm run gen:api-types"
    )
