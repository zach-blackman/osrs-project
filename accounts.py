"""Password hashing and opaque token helpers for Clan Tools accounts."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def hash_password(password: str) -> str:
    """PBKDF2-SHA256 with a random salt. Format: pbkdf2$iterations$salt$hex."""
    iterations = 260_000
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored or not password:
        return False
    try:
        kind, iters_s, salt, hexdigest = stored.split("$", 3)
        if kind != "pbkdf2":
            return False
        iterations = int(iters_s)
    except (ValueError, AttributeError):
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return hmac.compare_digest(digest.hex(), hexdigest)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


def new_invite_token() -> str:
    return secrets.token_urlsafe(24)


def new_ingest_token() -> tuple[str, str, str]:
    """Returns (plaintext, sha256 hex, short prefix for UI)."""
    plain = secrets.token_urlsafe(32)
    return plain, hash_token(plain), plain[:6]
