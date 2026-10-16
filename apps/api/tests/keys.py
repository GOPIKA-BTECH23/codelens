"""A throwaway RSA key for GitHub App tests, generated once per test run (never on disk).

Not named test_*: pytest would collect imported test_* functions as tests.
"""

from functools import lru_cache

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey


@lru_cache
def app_private_key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def app_private_key_pem() -> str:
    return (
        app_private_key()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,  # what GitHub's .pem download uses
            serialization.NoEncryption(),
        )
        .decode()
    )
