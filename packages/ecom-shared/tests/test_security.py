"""Tests for the shared cryptographic helpers.

These cover the properties that must hold for the whole system to be safe. If
one of these fails, several services are insecure at once.
"""

from __future__ import annotations

import time

import pytest

from ecom_shared.errors import UnauthorizedError
from ecom_shared.security import (
    constant_time_compare,
    create_jwt,
    decode_jwt,
    generate_token,
    hash_password,
    hash_token,
    verify_password,
)

SECRET = "x" * 64
OTHER_SECRET = "y" * 64
SUBJECT = "11111111-1111-1111-1111-111111111111"


class TestPasswordHashing:
    def test_hash_is_not_the_password(self):
        password = "marble-colonnade-77"
        assert hash_password(password) != password

    def test_verify_accepts_correct_password(self):
        assert verify_password("marble-colonnade-77", hash_password("marble-colonnade-77"))

    def test_verify_rejects_wrong_password(self):
        assert not verify_password("wrong", hash_password("marble-colonnade-77"))

    def test_same_password_hashes_differently(self):
        """Argon2 salts each hash, so two users with the same password do not
        share a hash. Without this, a single cracked hash would expose every
        account that reused that password."""
        assert hash_password("same-password-12") != hash_password("same-password-12")

    def test_verify_survives_a_corrupt_stored_hash(self):
        """A malformed hash must return False, not raise. One bad row should
        not turn every login attempt into a 500."""
        assert not verify_password("anything", "not-a-valid-argon2-hash")

    def test_uses_argon2id(self):
        """Argon2id specifically: `argon2i` and `argon2d` each sacrifice one of
        the two resistances we want."""
        assert hash_password("marble-colonnade-77").startswith("$argon2id$")


class TestTokens:
    def test_generated_tokens_are_unique(self):
        tokens = {generate_token() for _ in range(500)}
        assert len(tokens) == 500

    def test_generated_tokens_are_url_safe(self):
        """Tokens travel in reset links and cookies; a '+' or '/' would be
        mangled by URL encoding somewhere along the way."""
        token = generate_token()
        assert all(c.isalnum() or c in "-_" for c in token)

    def test_hash_is_deterministic_and_irreversible(self):
        token = generate_token()
        assert hash_token(token) == hash_token(token)
        assert token not in hash_token(token)
        assert len(hash_token(token)) == 64  # SHA-256 hex

    def test_constant_time_compare(self):
        assert constant_time_compare("abc", "abc")
        assert not constant_time_compare("abc", "abd")
        assert not constant_time_compare("abc", "abcd")


class TestJwt:
    def test_round_trip_preserves_claims(self):
        token = create_jwt(
            subject=SUBJECT,
            token_type="access",
            secret_key=SECRET,
            ttl_seconds=60,
            extra_claims={"role": "admin", "email": "a@b.com"},
        )
        claims = decode_jwt(token, secret_key=SECRET, expected_type="access")
        assert claims["sub"] == SUBJECT
        assert claims["role"] == "admin"

    def test_rejects_wrong_signing_key(self):
        """A forged token signed with any other key must not verify."""
        token = create_jwt(
            subject=SUBJECT, token_type="access", secret_key=SECRET, ttl_seconds=60
        )
        with pytest.raises(UnauthorizedError):
            decode_jwt(token, secret_key=OTHER_SECRET, expected_type="access")

    def test_rejects_type_confusion(self):
        """A refresh token must not be usable as an access token. Without the
        `typ` check, a long-lived credential would be accepted everywhere a
        15-minute one is, defeating the entire point of short access tokens."""
        refresh = create_jwt(
            subject=SUBJECT, token_type="refresh", secret_key=SECRET, ttl_seconds=60
        )
        with pytest.raises(UnauthorizedError):
            decode_jwt(refresh, secret_key=SECRET, expected_type="access")

    def test_rejects_expired_token(self):
        token = create_jwt(
            subject=SUBJECT, token_type="access", secret_key=SECRET, ttl_seconds=60
        )
        # Move the clock forward rather than sleeping for 60 seconds.
        import jwt as pyjwt

        claims = pyjwt.decode(token, SECRET, algorithms=["HS256"])
        claims["exp"] = int(time.time()) - 10
        expired = pyjwt.encode(claims, SECRET, algorithm="HS256")
        with pytest.raises(UnauthorizedError):
            decode_jwt(expired, secret_key=SECRET, expected_type="access")

    def test_rejects_alg_none_forgery(self):
        """The classic JWT attack: re-encode the token with "alg": "none" and
        no signature. Pinning the algorithm list on decode is what stops it."""
        import base64
        import json

        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b"=")
        payload = base64.urlsafe_b64encode(
            json.dumps(
                {"sub": SUBJECT, "typ": "access", "role": "admin",
                 "exp": int(time.time()) + 600, "iat": int(time.time())}
            ).encode()
        ).rstrip(b"=")
        forged = f"{header.decode()}.{payload.decode()}."

        with pytest.raises(UnauthorizedError):
            decode_jwt(forged, secret_key=SECRET, expected_type="access")

    def test_tokens_have_unique_ids(self):
        """Each token carries a distinct `jti`, so an individual session can be
        identified in an audit trail."""
        ids = {
            decode_jwt(
                create_jwt(
                    subject=SUBJECT, token_type="access", secret_key=SECRET, ttl_seconds=60
                ),
                secret_key=SECRET,
                expected_type="access",
            )["jti"]
            for _ in range(20)
        }
        assert len(ids) == 20
