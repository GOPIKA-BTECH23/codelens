"""Constants shared by every module that calls GitHub's REST API."""

GITHUB_WEB = "https://github.com"
GITHUB_API = "https://api.github.com"
# REST API version header. Verified against the docs on 2026-10-16 (the access-token
# endpoint documents X-GitHub-Api-Version: 2026-03-10).
GITHUB_API_VERSION = "2026-03-10"
# GitHub rejects API requests without a User-Agent and asks that it name the app.
USER_AGENT = "CodeLens"


def api_headers(
    token: str | None = None, *, accept: str = "application/vnd.github+json"
) -> dict[str, str]:
    """Standard headers for a REST call. `token` is a user, installation or app JWT token."""
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers
