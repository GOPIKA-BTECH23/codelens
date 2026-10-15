"""Token primitives: JWTs, OAuth state tokens, PKCE, refresh-token hashing."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.errors import InvalidTokenError, TokenExpiredError
from app.core.security import (
    ACCESS_AUDIENCE,
    ISSUER,
    OAuthAttempt,
    create_access_token,
    create_oauth_state_token,
    decode_access_token,
    decode_oauth_state_token,
    hash_token,
    new_oauth_attempt,
    new_refresh_token,
    pkce_challenge,
    states_match,
)
from tests.app_utils import make_settings

SETTINGS = make_settings()


def test_access_token_round_trip() -> None:
    user_id = uuid.uuid4()
    assert decode_access_token(create_access_token(user_id, SETTINGS), SETTINGS) == user_id


def test_access_token_contains_only_identity_claims() -> None:
    token = create_access_token(uuid.uuid4(), SETTINGS)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert set(claims) == {"sub", "iss", "aud", "iat", "exp"}  # no roles, no PII


def test_expired_access_token_is_rejected() -> None:
    issued = datetime.now(UTC) - timedelta(minutes=SETTINGS.jwt_access_ttl_minutes + 1)
    token = create_access_token(uuid.uuid4(), SETTINGS, now=issued)
    with pytest.raises(TokenExpiredError):
        decode_access_token(token, SETTINGS)


def test_token_signed_with_another_secret_is_rejected() -> None:
    other = make_settings(jwt_secret="another-secret-that-is-long-enough-1234")
    with pytest.raises(InvalidTokenError):
        decode_access_token(create_access_token(uuid.uuid4(), other), SETTINGS)


def test_unsigned_alg_none_token_is_rejected() -> None:
    now = datetime.now(UTC)
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "iss": ISSUER,
            "aud": ACCESS_AUDIENCE,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        key=None,
        algorithm="none",
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(forged, SETTINGS)


def test_oauth_state_token_cannot_be_used_as_an_access_token() -> None:
    """Different audiences: one signed token type can never stand in for another."""
    state_token = create_oauth_state_token(new_oauth_attempt(), SETTINGS)
    with pytest.raises(InvalidTokenError):
        decode_access_token(state_token, SETTINGS)


def test_oauth_state_round_trip_and_tamper_detection() -> None:
    attempt = OAuthAttempt(state="s" * 43, code_verifier="v" * 64)
    token = create_oauth_state_token(attempt, SETTINGS)
    assert decode_oauth_state_token(token, SETTINGS) == attempt

    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}.{signature[:-2]}AA"
    with pytest.raises(InvalidTokenError):
        decode_oauth_state_token(tampered, SETTINGS)


def test_new_oauth_attempts_are_random_and_pkce_sized() -> None:
    a, b = new_oauth_attempt(), new_oauth_attempt()
    assert a.state != b.state and a.code_verifier != b.code_verifier
    assert 43 <= len(a.code_verifier) <= 128  # RFC 7636 section 4.1


def test_pkce_challenge_matches_rfc_7636_example() -> None:
    # Appendix B of RFC 7636 (values copied from the RFC, checked 2026-10-16).
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_states_match() -> None:
    assert states_match("abc", "abc")
    assert not states_match("abc", "abd")
    assert not states_match("abc", "")


def test_refresh_tokens_are_random_and_hashed_deterministically() -> None:
    a, b = new_refresh_token(), new_refresh_token()
    assert a != b and len(a) >= 43
    assert hash_token(a) == hash_token(a)
    assert len(hash_token(a)) == 64 and a not in hash_token(a)
