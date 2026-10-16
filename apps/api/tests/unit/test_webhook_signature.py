"""verify_signature against GitHub's published test vector and the ways it must fail."""

import pytest

from app.core.webhook_signature import compute_signature, verify_signature

# From GitHub's "Validating webhook deliveries" page (checked 2026-10-16).
DOCS_SECRET = "It's a Secret to Everybody"
DOCS_PAYLOAD = b"Hello, World!"
DOCS_SIGNATURE = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"


def test_matches_githubs_published_test_vector() -> None:
    assert compute_signature(DOCS_SECRET, DOCS_PAYLOAD) == DOCS_SIGNATURE
    assert verify_signature(DOCS_SECRET, DOCS_PAYLOAD, DOCS_SIGNATURE) is True


@pytest.mark.parametrize(
    ("secret", "body", "header"),
    [
        (DOCS_SECRET, b"Hello, World?", DOCS_SIGNATURE),  # tampered body
        (DOCS_SECRET, b"Hello, World! ", DOCS_SIGNATURE),  # one extra byte
        ("another secret", DOCS_PAYLOAD, DOCS_SIGNATURE),  # wrong secret
        (DOCS_SECRET, DOCS_PAYLOAD, None),  # header missing
        (DOCS_SECRET, DOCS_PAYLOAD, ""),  # header empty
        (DOCS_SECRET, DOCS_PAYLOAD, DOCS_SIGNATURE.removeprefix("sha256=")),  # no prefix
        (DOCS_SECRET, DOCS_PAYLOAD, DOCS_SIGNATURE.replace("sha256=", "sha1=")),
        (DOCS_SECRET, DOCS_PAYLOAD, DOCS_SIGNATURE.upper()),  # hex digest is lowercase
        (DOCS_SECRET, DOCS_PAYLOAD, DOCS_SIGNATURE[:-1]),  # truncated
        (DOCS_SECRET, DOCS_PAYLOAD, "sha256=" + "é" * 64),  # non-ASCII must not crash
    ],
)
def test_rejects_anything_but_the_exact_signature(
    secret: str, body: bytes, header: str | None
) -> None:
    assert verify_signature(secret, body, header) is False


def test_reformatted_json_with_the_same_meaning_is_rejected() -> None:
    """Why we verify the RAW body: re-serialising JSON changes the bytes, so the
    signature of a parsed-and-redumped payload never matches."""
    raw = b'{"action":"opened","number":1}'
    reformatted = b'{"action": "opened", "number": 1}'
    signature = compute_signature(DOCS_SECRET, raw)
    assert verify_signature(DOCS_SECRET, raw, signature) is True
    assert verify_signature(DOCS_SECRET, reformatted, signature) is False
