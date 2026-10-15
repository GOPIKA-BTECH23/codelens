"""Token primitives and session cookies.

Three kinds of token, each with its own audience so one can never be replayed as another:
- access token: a short-lived JWT carrying only the user id (roles are read from the
  database on every request, so a removed membership takes effect immediately);
- OAuth state token: a JWT holding the `state` and PKCE verifier for one login attempt;
- refresh token: NOT a JWT. 32 random bytes; only its SHA-256 is stored (refresh_tokens).

Every token lives in an httpOnly cookie, so JavaScript (and therefore XSS) cannot read it.
Nothing in this module logs a token.
"""

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from starlette.responses import Response

from app.config import PUBLIC_API_PREFIX, Settings
from app.core.errors import InvalidTokenError, TokenExpiredError

JWT_ALGORITHM = "HS256"
ISSUER = "codelens"
ACCESS_AUDIENCE = "codelens:access"
OAUTH_STATE_AUDIENCE = "codelens:oauth-state"

ACCESS_COOKIE = "cl_access"
REFRESH_COOKIE = "cl_refresh"
OAUTH_COOKIE = "cl_oauth"

# Cookie paths are what the BROWSER sees (with the /api prefix the proxy strips).
# The refresh token is only ever sent to the auth endpoints, never to ordinary API calls.
REFRESH_COOKIE_PATH = f"{PUBLIC_API_PREFIX}/auth"
OAUTH_COOKIE_PATH = f"{PUBLIC_API_PREFIX}/auth/github"
# GitHub's authorization code lives 10 minutes; the login attempt cannot outlive it.
OAUTH_STATE_TTL = timedelta(minutes=10)


def _now() -> datetime:
    return datetime.now(UTC)


# --- access tokens ------------------------------------------------------------------------


def create_access_token(user_id: uuid.UUID, settings: Settings, now: datetime | None = None) -> str:
    """Sign a short-lived access JWT for `user_id`."""
    issued = now or _now()
    claims = {
        "sub": str(user_id),
        "iss": ISSUER,
        "aud": ACCESS_AUDIENCE,
        "iat": issued,
        "exp": issued + timedelta(minutes=settings.jwt_access_ttl_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, settings: Settings) -> uuid.UUID:
    """Return the user id in a valid access token.

    Raises TokenExpiredError or InvalidTokenError (both 401). The algorithm list is fixed,
    so a token claiming "alg": "none" or an RS256/HS256 confusion is rejected.
    """
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            audience=ACCESS_AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
        return uuid.UUID(claims["sub"])
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise InvalidTokenError() from exc


# --- OAuth state + PKCE ---------------------------------------------------------------------


@dataclass(frozen=True)
class OAuthAttempt:
    """What we remember between /auth/github/login and the callback."""

    state: str
    code_verifier: str


def new_oauth_attempt() -> OAuthAttempt:
    """Fresh random `state` and PKCE verifier for one login attempt."""
    # 48 random bytes -> 64 URL-safe characters (PKCE allows 43-128).
    return OAuthAttempt(state=secrets.token_urlsafe(32), code_verifier=secrets.token_urlsafe(48))


def pkce_challenge(code_verifier: str) -> str:
    """S256 code challenge: BASE64URL(SHA256(verifier)) without padding (RFC 7636)."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def create_oauth_state_token(attempt: OAuthAttempt, settings: Settings) -> str:
    """Sign the attempt so the callback can trust the cookie came from us, unmodified."""
    now = _now()
    claims = {
        "state": attempt.state,
        "cv": attempt.code_verifier,
        "iss": ISSUER,
        "aud": OAUTH_STATE_AUDIENCE,
        "iat": now,
        "exp": now + OAUTH_STATE_TTL,
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=JWT_ALGORITHM)


def decode_oauth_state_token(token: str, settings: Settings) -> OAuthAttempt:
    """Raises InvalidTokenError when the cookie is forged, tampered with or expired."""
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            audience=OAUTH_STATE_AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "state", "cv"]},
        )
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError("Login attempt expired or invalid") from exc
    return OAuthAttempt(state=str(claims["state"]), code_verifier=str(claims["cv"]))


def states_match(expected: str, received: str) -> bool:
    """Constant-time comparison, so response timing reveals nothing about the expected state."""
    return secrets.compare_digest(expected.encode(), received.encode())


# --- refresh tokens ------------------------------------------------------------------------


def new_refresh_token() -> str:
    """32 random bytes, URL-safe. Unguessable, so a plain hash (no salt) is enough to store."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex digest. A database leak then exposes no usable refresh tokens."""
    return hashlib.sha256(token.encode()).hexdigest()


# --- cookies -------------------------------------------------------------------------------


def _set_cookie(
    response: Response, settings: Settings, name: str, value: str, *, max_age: int, path: str
) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path=path,
        httponly=True,  # invisible to document.cookie, so XSS cannot steal it
        secure=settings.cookie_secure,
        samesite="lax",  # not sent on cross-site POSTs (CSRF layer 1); see ADR 0002
    )


def _delete_cookie(response: Response, settings: Settings, name: str, *, path: str) -> None:
    # Path and flags must match the original cookie or the browser keeps it.
    response.delete_cookie(
        name, path=path, httponly=True, secure=settings.cookie_secure, samesite="lax"
    )


def set_session_cookies(
    response: Response,
    settings: Settings,
    *,
    access_token: str,
    refresh_token: str,
    refresh_expires_at: datetime,
) -> None:
    """Attach both session cookies. The access cookie dies with its JWT; the refresh cookie
    dies with the session's absolute expiry."""
    _set_cookie(
        response,
        settings,
        ACCESS_COOKIE,
        access_token,
        max_age=settings.jwt_access_ttl_minutes * 60,
        path="/",
    )
    refresh_max_age = max(0, int((refresh_expires_at - _now()).total_seconds()))
    _set_cookie(
        response,
        settings,
        REFRESH_COOKIE,
        refresh_token,
        max_age=refresh_max_age,
        path=REFRESH_COOKIE_PATH,
    )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    _delete_cookie(response, settings, ACCESS_COOKIE, path="/")
    _delete_cookie(response, settings, REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)


def set_oauth_cookie(response: Response, settings: Settings, token: str) -> None:
    _set_cookie(
        response,
        settings,
        OAUTH_COOKIE,
        token,
        max_age=int(OAUTH_STATE_TTL.total_seconds()),
        path=OAUTH_COOKIE_PATH,
    )


def clear_oauth_cookie(response: Response, settings: Settings) -> None:
    """One login attempt, one state: the cookie is deleted whether the callback succeeds."""
    _delete_cookie(response, settings, OAUTH_COOKIE, path=OAUTH_COOKIE_PATH)
