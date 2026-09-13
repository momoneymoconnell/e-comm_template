"""Request and response models for the auth API.

The password rules live here, in :class:`PasswordField`, so registration,
password change and password reset cannot drift apart — three places enforcing
"the same" policy is three places for one of them to be weaker.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from ecom_shared.schemas import ApiModel
from ecom_shared.security import MIN_PASSWORD_LENGTH
from pydantic import EmailStr, Field, StringConstraints, field_validator

#: A password field with length bounds applied consistently.
#:
#: Minimum 12 characters, following NIST SP 800-63B: length beats composition
#: rules, which mostly teach users to write `Summer2024!`.
#:
#: Maximum 128 characters is a denial-of-service guard, not a policy. Argon2
#: hashing cost grows with input length, so an unbounded field lets someone
#: post a 10 MB "password" and burn a CPU core per request.
PasswordField = Annotated[str, StringConstraints(min_length=MIN_PASSWORD_LENGTH, max_length=128)]

#: The most-abused passwords in every credential-stuffing list. Cheap to check,
#: and it blocks the passwords that actually get accounts taken over. A real
#: deployment should extend this with a Have I Been Pwned range query.
COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "passw0rd",
        "123456",
        "12345678",
        "123456789",
        "1234567890",
        "qwerty",
        "qwerty123",
        "abc123",
        "111111",
        "letmein",
        "welcome",
        "admin",
        "admin123",
        "iloveyou",
        "monkey",
        "dragon",
        "sunshine",
        "princess",
        "football",
        "baseball",
        "trustno1",
        "changeme",
        "secret",
        "passw0rd1",
        "qwertyuiop",
        "asdfghjkl",
    }
)


def _reject_weak_password(value: str) -> str:
    """Reject passwords that are long but trivially guessable.

    Length alone is not sufficient: ``passwordpassword`` clears a 12-character
    minimum and is in every wordlist. This catches the obvious cases without
    imposing composition rules that push users toward predictable patterns.

    Args:
        value: The candidate password.

    Returns:
        The password, unchanged, if acceptable.

    Raises:
        ValueError: If the password is a known-common choice or a single
            repeated character.
    """
    lowered = value.lower()

    # Three cheap normalisations, because a wordlist lookup on the raw string
    # misses the variations people actually reach for:
    #   1. the word itself                      -> "password"
    #   2. the word with padding stripped       -> "password123!" -> "password"
    #   3. the word simply repeated             -> "passwordpassword" -> "password"
    stripped = lowered.strip("0123456789!@#$%^&*()_+-=[]{}|;:,.<>?/~`\\'\"")
    candidates = {lowered, stripped}
    for common in COMMON_PASSWORDS:
        # A whole-string repetition collapses to its unit.
        if (
            common
            and lowered == common * (len(lowered) // len(common))
            and len(lowered) % len(common) == 0
        ):
            candidates.add(common)
    if candidates & COMMON_PASSWORDS:
        raise ValueError("This password is too common. Choose something less predictable.")
    # "aaaaaaaaaaaa" passes a length check but has ~0 bits of entropy.
    if len(set(value)) < 5:
        raise ValueError("This password does not have enough variety. Mix in more characters.")
    return value


# -----------------------------------------------------------------------------
# Requests
# -----------------------------------------------------------------------------


class RegisterRequest(ApiModel):
    """Create a new customer account."""

    email: EmailStr = Field(description="Email address. Case-insensitive.")
    password: PasswordField = Field(description=f"At least {MIN_PASSWORD_LENGTH} characters.")
    full_name: str | None = Field(default=None, max_length=200, description="Display name.")

    @field_validator("password")
    @classmethod
    def _check_password_strength(cls, value: str) -> str:
        """Apply the shared weak-password rules."""
        return _reject_weak_password(value)


class LoginRequest(ApiModel):
    """Exchange credentials for a session."""

    email: EmailStr
    # No length constraints here. Validating the *shape* of a submitted
    # password would tell an attacker your policy before they have an account,
    # and would reject a legitimate user whose password predates a policy change.
    password: str = Field(max_length=128)


class RefreshRequest(ApiModel):
    """Exchange a refresh token for a new pair.

    The token normally arrives in an httpOnly cookie, which the browser sends
    automatically. This body is the fallback for non-browser clients.
    """

    refresh_token: str | None = Field(default=None, description="Only for non-browser clients.")


class ChangePasswordRequest(ApiModel):
    """Change your own password while signed in."""

    current_password: str = Field(max_length=128)
    new_password: PasswordField

    @field_validator("new_password")
    @classmethod
    def _check_password_strength(cls, value: str) -> str:
        """Apply the shared weak-password rules."""
        return _reject_weak_password(value)


class ForgotPasswordRequest(ApiModel):
    """Request a reset link."""

    email: EmailStr


class ResetPasswordRequest(ApiModel):
    """Redeem a reset link."""

    token: str = Field(min_length=20, max_length=200)
    new_password: PasswordField

    @field_validator("new_password")
    @classmethod
    def _check_password_strength(cls, value: str) -> str:
        """Apply the shared weak-password rules."""
        return _reject_weak_password(value)


class VerifyEmailRequest(ApiModel):
    """Redeem an email verification link."""

    token: str = Field(min_length=20, max_length=200)


class UpdateProfileRequest(ApiModel):
    """Update your own profile.

    Only `full_name` is here, on purpose. Email changes need a verification
    flow, and `role` must never be settable by its own owner — the absence of
    those fields, combined with ``extra="forbid"``, is what makes privilege
    escalation via this endpoint impossible rather than merely unlikely.
    """

    full_name: str | None = Field(default=None, max_length=200)


class AdminUpdateUserRequest(ApiModel):
    """Admin-only changes to another user's account.

    Attributes:
        is_active: Enable or disable sign-in.
        role: Promote or demote. Setting ``admin`` still requires the address to
            be on the ``ADMIN_EMAILS`` allowlist, so a compromised admin session
            cannot mint further admins on its own.
    """

    is_active: bool | None = None
    role: str | None = Field(default=None, pattern="^(customer|admin)$")


# -----------------------------------------------------------------------------
# Responses
# -----------------------------------------------------------------------------


class UserResponse(ApiModel):
    """A user, as returned to that user or to an admin.

    Note what is absent: `password_hash`. Building responses from an explicit
    model rather than serialising the ORM object is what guarantees a future
    column cannot quietly start appearing in API output.
    """

    id: UUID
    email: EmailStr
    full_name: str | None
    role: str
    is_active: bool
    email_verified_at: datetime | None
    last_login_at: datetime | None
    created_at: datetime


class SessionResponse(ApiModel):
    """Returned after a successful login or refresh.

    The tokens themselves are set as httpOnly cookies and are deliberately not
    in this body — a token in a JSON response can be read by any script on the
    page, which is the exact exposure httpOnly cookies exist to prevent.

    Attributes:
        user: The signed-in user.
        expires_in: Access token lifetime in seconds, so the client can refresh
            proactively instead of waiting for a 401.
        csrf_token: Value the client must echo in the ``X-CSRF-Token`` header on
            state-changing requests.
    """

    user: UserResponse
    expires_in: int
    csrf_token: str


class SessionSummary(ApiModel):
    """One active session, for the "where am I signed in" list."""

    id: UUID
    created_at: datetime
    expires_at: datetime
    user_agent: str | None
    ip_address: str | None
    is_current: bool


class AuditEventResponse(ApiModel):
    """One audit trail entry, for the admin console."""

    id: UUID
    actor_user_id: UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    ip_address: str | None
    event_metadata: dict[str, Any]
    created_at: datetime
