"""
Intelligence Operating System — Security Utilities
==================================================
Centralises every cryptographic operation:
  - JWT access-token and refresh-token creation / validation
  - Password hashing and verification via bcrypt (passlib)
  - API key generation and hash comparison
  - OAuth2 bearer scheme declaration for FastAPI dependency injection

Nothing else in the codebase should import ``jose``, ``passlib``, or
``secrets`` for security-sensitive operations; use the functions here.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings
from app.core.constants import (
    API_KEY_PREFIX_LENGTH,
    API_KEY_TOTAL_LENGTH,
)
from app.core.exceptions import TokenExpiredError, TokenInvalidError
from app.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Password hashing context
# ---------------------------------------------------------------------------

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    """
    Hash a plain-text password using bcrypt.

    Args:
        plain_password: Raw password string from user input.

    Returns:
        bcrypt hash string suitable for database storage.
    """
    settings = get_settings()
    # passlib reads rounds from the context; we override via deprecated kwarg trick
    # by re-creating context with the configured rounds when needed.
    ctx = CryptContext(
        schemes=["bcrypt"],
        deprecated="auto",
        bcrypt__rounds=settings.bcrypt_rounds,
    )
    return ctx.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain-text password against its bcrypt hash.

    Args:
        plain_password: Candidate password.
        hashed_password: Stored bcrypt hash.

    Returns:
        ``True`` if the password matches, ``False`` otherwise.
    """
    return _pwd_context.verify(plain_password, hashed_password)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

_TokenType = Literal["access", "refresh"]


def _build_token(
    subject: str,
    token_type: _TokenType,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """
    Internal helper: create and sign a JWT.

    Args:
        subject: ``sub`` claim — typically the user UUID as a string.
        token_type: ``"access"`` or ``"refresh"``.
        extra_claims: Additional claims merged into the payload.

    Returns:
        Compact serialised JWT string.
    """
    settings = get_settings()
    now = datetime.now(tz=timezone.utc)

    if token_type == "access":
        expire = now + timedelta(minutes=settings.jwt.access_token_expire_minutes)
    else:
        expire = now + timedelta(days=settings.jwt.refresh_token_expire_days)

    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": expire,
        "jti": str(uuid.uuid4()),  # Unique token ID (for revocation tracking)
        "type": token_type,
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(
        payload,
        settings.jwt.secret_key.get_secret_value(),
        algorithm=settings.jwt.algorithm,
    )


def create_access_token(
    subject: str,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """
    Create a short-lived JWT access token.

    Args:
        subject: Unique identifier for the principal (user UUID).
        extra_claims: Optional additional claims (e.g., roles, scopes).

    Returns:
        Signed JWT string.
    """
    return _build_token(subject, "access", extra_claims)


def create_refresh_token(subject: str) -> str:
    """
    Create a long-lived JWT refresh token.

    Refresh tokens carry no roles/scopes — they are single-purpose tokens
    for obtaining a new access token pair.

    Args:
        subject: Unique identifier for the principal (user UUID).

    Returns:
        Signed JWT string.
    """
    return _build_token(subject, "refresh")


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Validate and decode a JWT access token.

    Args:
        token: Compact serialised JWT string.

    Returns:
        Decoded payload dictionary.

    Raises:
        TokenExpiredError: The token's ``exp`` claim is in the past.
        TokenInvalidError: The token is malformed, signature mismatch,
                           or is not an access token.
    """
    return _decode_token(token, expected_type="access")


def decode_refresh_token(token: str) -> dict[str, Any]:
    """
    Validate and decode a JWT refresh token.

    Args:
        token: Compact serialised JWT string.

    Returns:
        Decoded payload dictionary.

    Raises:
        TokenExpiredError: The token has expired.
        TokenInvalidError: The token is malformed or not a refresh token.
    """
    return _decode_token(token, expected_type="refresh")


def _decode_token(token: str, expected_type: _TokenType) -> dict[str, Any]:
    """Validate, decode, and type-check a JWT."""
    settings = get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt.secret_key.get_secret_value(),
            algorithms=[settings.jwt.algorithm],
            options={"require": ["sub", "exp", "iat", "jti", "type"]},
        )
    except JWTError as exc:
        # jose raises ExpiredSignatureError (a JWTError subclass) for expiry
        exc_str = str(exc).lower()
        if "expired" in exc_str or "expiration" in exc_str:
            logger.warning("jwt_token_expired", exc=str(exc))
            raise TokenExpiredError("Token has expired.") from exc
        logger.warning("jwt_token_invalid", exc=str(exc))
        raise TokenInvalidError(f"Token is invalid: {exc}") from exc

    token_type = payload.get("type")
    if token_type != expected_type:
        raise TokenInvalidError(
            f"Expected token type '{expected_type}', got '{token_type}'."
        )

    return payload


def extract_subject(token: str) -> str:
    """
    Extract the ``sub`` claim from a token *without* verifying the signature.

    **Only use this for diagnostic logging or UI display — never for
    security decisions.**

    Args:
        token: JWT string.

    Returns:
        Subject claim string.

    Raises:
        TokenInvalidError: Token is malformed and cannot be decoded at all.
    """
    try:
        payload = jwt.get_unverified_claims(token)
        return str(payload["sub"])
    except (JWTError, KeyError) as exc:
        raise TokenInvalidError("Cannot extract subject from token.") from exc


# ---------------------------------------------------------------------------
# API Key utilities
# ---------------------------------------------------------------------------


def generate_api_key() -> tuple[str, str]:
    """
    Generate a new API key and its SHA-256 hash.

    The full key is returned to the caller **once** for display to the user.
    Only the hash should be stored in the database.

    Returns:
        A 2-tuple of ``(raw_key, key_hash)`` where:
        - ``raw_key`` is a 64-character URL-safe random string.
        - ``key_hash`` is the SHA-256 hex digest of the raw key.
    """
    raw_key = secrets.token_urlsafe(API_KEY_TOTAL_LENGTH)
    key_hash = _hash_api_key(raw_key)
    return raw_key, key_hash


def _hash_api_key(raw_key: str) -> str:
    """
    Compute the SHA-256 hash of an API key.

    Args:
        raw_key: Plain-text API key string.

    Returns:
        Hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(raw_key.encode()).hexdigest()


def verify_api_key(raw_key: str, stored_hash: str) -> bool:
    """
    Constant-time comparison of an API key against its stored hash.

    Args:
        raw_key: Candidate key provided by the caller.
        stored_hash: SHA-256 hash from the database.

    Returns:
        ``True`` if the key matches.
    """
    candidate_hash = _hash_api_key(raw_key)
    return secrets.compare_digest(candidate_hash, stored_hash)


def get_api_key_prefix(raw_key: str) -> str:
    """
    Return the display prefix of an API key (first 8 characters).

    This prefix is shown in the UI so the user can identify their keys
    without exposing the full secret.

    Args:
        raw_key: Full API key string.

    Returns:
        First ``API_KEY_PREFIX_LENGTH`` characters.
    """
    return raw_key[:API_KEY_PREFIX_LENGTH]


# ---------------------------------------------------------------------------
# OAuth CSRF state token
# ---------------------------------------------------------------------------


def generate_oauth_state() -> str:
    """
    Generate a cryptographically random state parameter for OAuth2 CSRF protection.

    Returns:
        32-byte URL-safe random string.
    """
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Refresh token hashing (stored in DB, compared on rotation)
# ---------------------------------------------------------------------------


def hash_refresh_token(raw_token: str) -> str:
    """
    Hash a refresh token for database storage.

    Args:
        raw_token: Plain JWT refresh token string.

    Returns:
        SHA-256 hex digest.
    """
    return hashlib.sha256(raw_token.encode()).hexdigest()


def verify_refresh_token_hash(raw_token: str, stored_hash: str) -> bool:
    """
    Constant-time verification of a refresh token against its stored hash.

    Args:
        raw_token: Raw JWT refresh token.
        stored_hash: SHA-256 hash from the database.

    Returns:
        ``True`` if the token matches the stored hash.
    """
    return secrets.compare_digest(
        hashlib.sha256(raw_token.encode()).hexdigest(),
        stored_hash,
    )
