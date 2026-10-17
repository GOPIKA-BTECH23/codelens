from typing import Any

from app.core.sentry import scrub_event


def _event() -> dict[str, Any]:
    return {
        "request": {
            "url": "https://x/api/auth/github/callback",
            "query_string": "code=abc&state=def",
            "cookies": {"cl_access": "jwt"},
            "data": {"message": "def secret(): ..."},
            "env": {"REMOTE_ADDR": "1.2.3.4"},
            "headers": {
                "Cookie": "cl_access=jwt",
                "Authorization": "Bearer t",
                "User-Agent": "curl",
            },
        },
        "user": {"ip_address": "1.2.3.4", "id": "42"},
        "breadcrumbs": {"values": [{"message": "SELECT ..."}]},
        "exception": {
            "values": [{"stacktrace": {"frames": [{"function": "f", "vars": {"k": "v"}}]}}]
        },
    }


def test_scrub_removes_credentials_bodies_identity_and_frame_variables() -> None:
    out = scrub_event(_event())

    assert out is not None
    request = out["request"]
    for gone in ("cookies", "data", "query_string", "env"):
        assert gone not in request
    assert request["headers"]["Cookie"] == "[scrubbed]"
    assert request["headers"]["Authorization"] == "[scrubbed]"
    assert request["headers"]["User-Agent"] == "curl"  # harmless headers stay useful
    assert "user" not in out
    assert "breadcrumbs" not in out
    assert "vars" not in out["exception"]["values"][0]["stacktrace"]["frames"][0]


def test_scrub_keeps_the_error_itself() -> None:
    out = scrub_event(_event())

    assert out is not None
    assert out["exception"]["values"][0]["stacktrace"]["frames"][0]["function"] == "f"
    assert out["request"]["url"] == "https://x/api/auth/github/callback"
