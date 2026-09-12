"""Password hashing, token signing, and constant-time comparisons.

This module is small on purpose. Cryptography is the one area where "clever"
is a synonym for "broken", so everything here delegates to a vetted library and
the only decisions we make are parameter choices — each of which is justified
in a comment.

What we store and what we never store:

* Passwords → Argon2id hash only. The plaintext is never logged, never cached,
  and never written to the database.
* Refresh tokens → SHA-256 of the token. A database dump therefore does not
  hand an attacker a set of working sessions.
* Card numbers → **never touched**. They go from the browser straight to Stripe
  via Stripe Elements. Our servers only ever see an opaque payment-intent ID,
  which is what keeps this template out of PCI-DSS scope.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from ecom_shared.errors import UnauthorizedError

# -----------------------------------------------------------------------------
# Password hashing
# -----------------------------------------------------------------------------
# Argon2id won the Password Hashing Competition and is the current OWASP first
# choice: unlike bcrypt it resists GPU *and* side-channel attacks, and unlike
# PBKDF2 it is memory-hard, so custom cracking hardware gains far less.
#
# Parameters follow the OWASP cheat-sheet baseline (19 MiB, 2 iterations,
# 1 degree of parallelism). Raising memory_cost is the most effective lever if
# you later want to make cracking more expensive; it costs ~19 MiB of RAM per
# concurrent login, so scale it against your container memory limit.
_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,  # KiB
    parallelism=1,
    hash_len=32,
    salt_len=16,
)

#: Minimum password length. NIST SP 800-63B recommends length over composition
#: rules ("must contain a symbol" pushes users toward `Password1!`), so this is
#: the only rule we enforce besides a breach-style blocklist in the auth service.
MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id.

    Args:
        password: The user's plaintext password.

    Returns:
        An encoded hash string that embeds the algorithm, parameters and salt,
        e.g. ``$argon2id$v=19$m=19456,t=2,p=1$...``. Store this verbatim; no
        separate salt column is needed.
    """
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext password against a stored hash.

    Argon2's own verification is constant-time with respect to the hash, so
    this does not leak information through timing.

    Args:
        password: The plaintext supplied at login.
        password_hash: The stored hash from :func:`hash_password`.

    Returns:
        ``True`` if the password matches. ``False`` for a mismatch *and* for a
        malformed or corrupt stored hash — a login attempt must never 500
        because of bad data in one row.
    """
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """Whether a stored hash was made with weaker parameters than we now use.

    Call this right after a successful login: you have the plaintext in hand
    for the only moment you legitimately can, so it is the one opportunity to
    silently upgrade the hash. This lets you raise the cost parameters over
    time without a forced password reset.

    Args:
        password_hash: The stored hash.

    Returns:
        ``True`` if the hash should be recomputed and saved.
    """
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# -----------------------------------------------------------------------------
# Opaque tokens (refresh tokens, email verification, password reset)
# -----------------------------------------------------------------------------


def generate_token(num_bytes: int = 32) -> str:
    """Generate a cryptographically secure, URL-safe random token.

    Args:
        num_bytes: Entropy in bytes. 32 bytes = 256 bits, far beyond brute
            force. Never lower this below 16.

    Returns:
        A URL-safe base64 string with no padding.
    """
    return secrets.token_urlsafe(num_bytes)


def hash_token(token: str) -> str:
    """Hash an opaque token for storage.

    Plain SHA-256 is correct here, *unlike* for passwords. Argon2's slowness
    exists to frustrate brute force against low-entropy human input; a 256-bit
    random token has no brute-force surface, so all we need is a one-way
    function — and a fast one, because this runs on every authenticated
    refresh.

    Args:
        token: The token as issued to the client.

    Returns:
        Lowercase hex SHA-256 digest. Store this; discard the original.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_compare(a: str, b: str) -> bool:
    """Compare two strings without leaking their similarity through timing.

    A naive ``==`` returns as soon as two bytes differ, so an attacker can
    measure response time to discover a secret one character at a time. Use
    this for anything secret: CSRF tokens, webhook signatures, API keys.

    Args:
        a: First value.
        b: Second value.

    Returns:
        ``True`` if the strings are identical.
    """
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# -----------------------------------------------------------------------------
# JWTs
# -----------------------------------------------------------------------------

TokenType = Literal["access", "refresh", "service"]
"""The three kinds of token in the system.

* ``access``  — a user's short-lived credential, sent on every request.
* ``refresh`` — a user's long-lived, revocable credential.
* ``service`` — one microservice proving its identity to another. Carries no
  user, so it can never be mistaken for one.

The type is embedded in the ``typ`` claim and checked on every decode. Keeping
them distinct is what stops a token issued for one purpose being replayed for
another — the single most common JWT implementation flaw.
"""


def create_jwt(
    *,
    subject: str,
    token_type: TokenType,
    secret_key: str,
    ttl_seconds: int,
    algorithm: str = "HS256",
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Mint a signed JWT.

    The payload carries the registered claims plus whatever `extra_claims` you
    add (we use ``role`` and ``email``).

    **JWT payloads are signed, not encrypted** — anyone holding the token can
    base64-decode and read every claim. Put identifiers in there, never
    secrets, and never anything you would not show the user.

    Args:
        subject: The ``sub`` claim — the user's ID.
        token_type: ``"access"`` or ``"refresh"``. Recorded in the ``typ``
            claim and checked on decode, so a refresh token cannot be replayed
            as an access token (a real and frequently exploited confusion bug).
        secret_key: HMAC signing key.
        ttl_seconds: Lifetime from now.
        algorithm: JWS algorithm. Keep HS256 unless you move to asymmetric keys.
        extra_claims: Additional claims to embed.

    Returns:
        The encoded, signed JWT.
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "typ": token_type,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
        # A unique token ID, so an individual token can be revoked or traced.
        "jti": secrets.token_urlsafe(16),
        **(extra_claims or {}),
    }
    return jwt.encode(payload, secret_key, algorithm=algorithm)


def decode_jwt(
    token: str,
    *,
    secret_key: str,
    expected_type: TokenType,
    algorithm: str = "HS256",
) -> dict[str, Any]:
    """Verify a JWT's signature and expiry, then return its claims.

    Args:
        token: The encoded JWT.
        secret_key: The same key used to sign it.
        expected_type: The ``typ`` this call site requires. A mismatch is
            rejected, which is what stops a long-lived refresh token from being
            used as an access token.
        algorithm: Permitted algorithm. Passed as a fixed single value so a
            forged header claiming ``"alg": "none"`` is rejected outright.

    Returns:
        The decoded claims.

    Raises:
        UnauthorizedError: If the signature is invalid, the token has expired,
            or the type does not match. The message is intentionally vague —
            telling an attacker *which* check failed is free information.
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            secret_key,
            algorithms=[algorithm],
            options={"require": ["exp", "iat", "sub", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Your session has expired. Please sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Invalid authentication credentials.") from exc

    if claims.get("typ") != expected_type:
        raise UnauthorizedError("Invalid authentication credentials.")
    return claims


def create_service_token(
    *,
    service_name: str,
    secret_key: str,
    ttl_seconds: int = 60,
    algorithm: str = "HS256",
) -> str:
    """Mint a token proving one service's identity to another.

    Used when a service must call an internal endpoint on its own behalf rather
    than a user's — orders asking catalog to price a cart, for example. The
    token carries no user, so an internal endpoint cannot be tricked into
    treating a service call as a customer's.

    The TTL is deliberately tiny. These tokens are minted per call and never
    stored, so a minute is ample, and it means a token captured from a log or a
    traffic dump is useless almost immediately.

    Args:
        service_name: The calling service, e.g. ``"orders"``. Becomes ``sub``.
        secret_key: The shared signing key.
        ttl_seconds: Lifetime. Keep it short.
        algorithm: JWS algorithm.

    Returns:
        The encoded service token.
    """
    return create_jwt(
        subject=f"service:{service_name}",
        token_type="service",  # noqa: S106 - a token *type* discriminator, not a secret
        secret_key=secret_key,
        ttl_seconds=ttl_seconds,
        algorithm=algorithm,
        extra_claims={"svc": service_name},
    )
