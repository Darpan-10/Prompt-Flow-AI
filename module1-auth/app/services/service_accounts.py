"""
Secret hashing for local M2M service accounts (dev/staging only).

Stdlib-only (hashlib.pbkdf2_hmac) so this doesn't need a new dependency
just for local development — Cognito remains the real credential store
in production (see app/services/cognito.py::verify_m2m_client).
"""
import hashlib
import hmac
import os

_ITERATIONS = 260_000


def hash_secret(secret: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, _ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_secret(secret: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
    except (ValueError, AttributeError):
        return False
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(digest_hex)
    actual = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, _ITERATIONS)
    return hmac.compare_digest(actual, expected)
