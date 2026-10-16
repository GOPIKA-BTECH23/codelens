"""GitHub webhook signature verification (X-Hub-Signature-256).

Verified against GitHub's docs ("Validating webhook deliveries") on 2026-10-16: GitHub
computes HMAC-SHA256 over the request body with the webhook secret and sends the hex
digest as `X-Hub-Signature-256: sha256=<hex>`. Compare in constant time. The older
`X-Hub-Signature` (HMAC-SHA1) header exists only for compatibility and is ignored here.
"""

import hashlib
import hmac

SIGNATURE_HEADER = "X-Hub-Signature-256"
_PREFIX = "sha256="


def compute_signature(secret: str, body: bytes) -> str:
    """The header value GitHub would send for `body`. Used by tests and the replay script."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{_PREFIX}{digest}"


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """True only if `header` is GitHub's signature of exactly these raw `body` bytes."""
    if not header or not header.startswith(_PREFIX):
        return False
    expected = compute_signature(secret, body)
    return hmac.compare_digest(expected.encode("ascii"), header.encode("utf-8"))
