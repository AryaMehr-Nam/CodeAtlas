"""Cryptographic hashing, token signing, and masking helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

_TOKEN_SECRET = os.getenv("SAMPLE_SAAS_TOKEN_SECRET", "dev-sample-saas-signing-secret")


def hash_password(password: str, salt: str | None = None) -> str:
    """Derive a salted PBKDF2-HMAC-SHA256 password digest."""
    chosen_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        chosen_salt.encode("utf-8"),
        120_000,
    ).hex()
    return f"pbkdf2_sha256${chosen_salt}${digest}"


def verify_password(password: str, encoded_hash: str) -> bool:
    """Verify a plaintext password against a stored PBKDF2 digest."""
    parts = encoded_hash.split("$")
    if len(parts) != 3 or parts[0] != "pbkdf2_sha256":
        return False
    _, salt, expected_digest = parts
    candidate = hash_password(password, salt=salt)
    return hmac.compare_digest(candidate, f"pbkdf2_sha256${salt}${expected_digest}")


def sign_token(subject: str, role: str, ttl_seconds: int = 3600) -> str:
    """Sign a compact HMAC token containing subject, role, and expiry."""
    payload = {
        "sub": subject,
        "role": role,
        "exp": int(time.time()) + ttl_seconds,
    }
    encoded_payload = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    signature = hmac.new(
        _TOKEN_SECRET.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded_payload}.{signature}"


def verify_token(token: str) -> dict[str, str] | None:
    """Validate token HMAC signature and expiration timestamp."""
    if "." not in token:
        return None
    encoded_payload, provided_sig = token.split(".", 1)
    expected_sig = hmac.new(
        _TOKEN_SECRET.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(provided_sig, expected_sig):
        return None

    try:
        raw_bytes = base64.urlsafe_b64decode(encoded_payload.encode("ascii"))
        claims = json.loads(raw_bytes.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None

    if int(claims.get("exp", 0)) < int(time.time()):
        return None
    return {"sub": str(claims["sub"]), "role": str(claims["role"])}


def generate_idempotency_key(prefix: str, seed: str) -> str:
    """Create a deterministic short idempotency key for billing writes."""
    digest = hashlib.sha256(f"{prefix}:{seed}".encode()).hexdigest()[:20]
    return f"{prefix}_{digest}"


def mask_sensitive_value(value: str, visible_chars: int = 4) -> str:
    """Mask emails or account identifiers while retaining suffix context."""
    cleaned = value.strip()
    if len(cleaned) <= visible_chars:
        return "*" * len(cleaned)
    return f"{'*' * (len(cleaned) - visible_chars)}{cleaned[-visible_chars:]}"
