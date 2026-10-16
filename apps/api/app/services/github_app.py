"""Authenticate AS the GitHub App: private key -> app JWT -> installation access token.

This is the second of our two auth systems (CLAUDE.md section 9). User login (M2) proves
who a person is; this proves that a request comes from the CodeLens App, and then asks
GitHub for a token that may act on ONE installation's repositories. Only the worker uses it.

Verified against GitHub's docs on 2026-10-16 ("Generating a JSON Web Token (JWT) for a
GitHub App" and REST "Create an installation access token for an app"):
- the JWT is signed with RS256; `iat` should be 60 s in the past (clock drift), `exp` at
  most 10 minutes ahead, `iss` the App's client ID (recommended) or app ID;
- POST /app/installations/{installation_id}/access_tokens with `Authorization: Bearer <JWT>`
  answers 201 with `token` and `expires_at`; the token lives one hour;
- since 2026-04-27 tokens may use a longer stateless format, so nothing here assumes a
  token length.
"""

import asyncio
import base64
import binascii
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from app.services.github_common import GITHUB_API, api_headers

# GitHub's JWT rules: iat backdated by 60 s, exp at most 10 minutes after "now" by GitHub's
# clock. 9 minutes leaves a minute of margin if our clock runs slightly fast.
JWT_BACKDATE = timedelta(seconds=60)
JWT_LIFETIME = timedelta(minutes=9)
# Fetch a fresh installation token once the cached one has less than this left, so a job
# never starts with a token that expires halfway through.
TOKEN_REFRESH_MARGIN = timedelta(minutes=5)

logger = logging.getLogger("app.github")


class GitHubAppError(Exception):
    """The App could not authenticate. The message is safe to log (no keys, no tokens)."""


def utc_now() -> datetime:
    return datetime.now(UTC)


# --- private key ---------------------------------------------------------------------------


def load_private_key(value: str) -> RSAPrivateKey:
    """Parse GITHUB_APP_PRIVATE_KEY, which may be any of:

    - the PEM text itself (also with literal `\\n` sequences, as some dashboards store it);
    - the PEM base64-encoded on one line (easiest for env vars and docker compose);
    - a path to the downloaded .pem file.

    Raises ValueError with a message that never contains the key.
    """
    text = value.strip()
    if "-----BEGIN" in text:
        pem = text.replace("\\n", "\n").encode()
    elif _is_file(text):
        pem = Path(text).read_bytes()
    else:
        try:
            pem = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError(
                "is not PEM text, base64-encoded PEM, or the path of an existing file"
            ) from None
        if b"-----BEGIN" not in pem:
            raise ValueError("base64 value does not decode to a PEM private key")
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError):
        # TypeError: the key is password-protected. GitHub's keys never are.
        raise ValueError("could not be parsed as an unencrypted PEM private key") from None
    if not isinstance(key, RSAPrivateKey):
        raise ValueError("must be an RSA key (GitHub App keys are RSA)")
    return key


def _is_file(text: str) -> bool:
    # A long base64 string is not a path; some systems raise instead of returning False.
    try:
        return len(text) < 1024 and Path(text).is_file()
    except OSError:
        return False


# --- app JWT -------------------------------------------------------------------------------


def create_app_jwt(client_id: str, private_key: RSAPrivateKey, *, now: datetime) -> str:
    """The short-lived JWT that authenticates as the App itself (not as an installation).

    It can only call /app/... endpoints, such as minting installation tokens.
    """
    payload = {
        "iat": int((now - JWT_BACKDATE).timestamp()),
        "exp": int((now + JWT_LIFETIME).timestamp()),
        "iss": client_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


# --- installation tokens -------------------------------------------------------------------


@dataclass(frozen=True)
class InstallationToken:
    token: str = field(repr=False)  # never in logs or reprs
    expires_at: datetime


class GitHubApp:
    """Mints and caches installation access tokens. One instance per process.

    The cache is a plain dict in memory: tokens are cheap to re-mint, so losing them on a
    restart costs one HTTP call per installation. A per-installation lock means that when
    several jobs for the same installation start together, only one of them asks GitHub.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        client_id: str,
        private_key: RSAPrivateKey,
        clock: Callable[[], datetime] = utc_now,
        refresh_margin: timedelta = TOKEN_REFRESH_MARGIN,
    ) -> None:
        self._http = http
        self._client_id = client_id
        self._private_key = private_key
        self._clock = clock
        self._refresh_margin = refresh_margin
        self._cache: dict[int, InstallationToken] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def app_jwt(self) -> str:
        return create_app_jwt(self._client_id, self._private_key, now=self._clock())

    async def installation_token(self, installation_id: int) -> str:
        """A token for `installation_id` that stays valid for at least the refresh margin."""
        cached = self._cache.get(installation_id)
        if cached is not None and self._is_fresh(cached):
            return cached.token
        lock = self._locks.setdefault(installation_id, asyncio.Lock())
        async with lock:
            # Another task may have refreshed it while we waited for the lock.
            cached = self._cache.get(installation_id)
            if cached is not None and self._is_fresh(cached):
                return cached.token
            fresh = await self._create_installation_token(installation_id)
            self._cache[installation_id] = fresh
            return fresh.token

    def invalidate(self, installation_id: int) -> None:
        """Forget a cached token (e.g. GitHub answered 401: it was revoked early)."""
        self._cache.pop(installation_id, None)

    def _is_fresh(self, token: InstallationToken) -> bool:
        return token.expires_at - self._clock() > self._refresh_margin

    async def _create_installation_token(self, installation_id: int) -> InstallationToken:
        """POST /app/installations/{id}/access_tokens. One attempt: the job retries."""
        url = f"{GITHUB_API}/app/installations/{installation_id}/access_tokens"
        try:
            response = await self._http.post(url, headers=api_headers(self.app_jwt()))
        except httpx.HTTPError as exc:
            raise GitHubAppError(
                f"installation token request failed: {type(exc).__name__}"
            ) from exc
        if response.status_code == 404:
            # Wrong id, or the App was uninstalled from that account.
            raise GitHubAppError(f"installation {installation_id} not found (uninstalled?)")
        if response.status_code == 401:
            raise GitHubAppError(
                "GitHub rejected the app JWT: check GITHUB_OAUTH_CLIENT_ID, the private key "
                "and this machine's clock"
            )
        if response.status_code != 201:
            raise GitHubAppError(f"installation token request returned HTTP {response.status_code}")
        body = response.json()
        try:
            token = body["token"]
            expires_at = datetime.fromisoformat(body["expires_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubAppError("installation token response is malformed") from exc
        if not isinstance(token, str) or not token:
            raise GitHubAppError("installation token response has no token")
        logger.info(
            "installation token minted",
            extra={"installation_id": installation_id, "expires_at": expires_at.isoformat()},
        )
        return InstallationToken(token=token, expires_at=expires_at)
