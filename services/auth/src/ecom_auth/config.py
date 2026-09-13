"""Configuration for the auth service."""

from __future__ import annotations

from ecom_shared.config import ServiceSettings
from pydantic import SecretStr, field_validator


class AuthSettings(ServiceSettings):
    """Settings for accounts, sessions and access control.

    Attributes:
        admin_emails: Comma-separated allowlist. Only an address on this list
            may ever hold the ``admin`` role. This is a second lock on top of
            the role column: even if an attacker found a way to write
            ``role = 'admin'`` to a row, the address must also appear here, and
            that requires access to the deployment environment rather than to
            the database.
        bootstrap_admin_email: Admin account created on first boot.
        bootstrap_admin_password: Its initial password. Change it after signing
            in; the value stays in your `.env` otherwise.
    """

    service_name: str = "auth"
    db_schema: str = "auth"

    admin_emails: str = ""
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: SecretStr | None = None

    # --- Cookies ------------------------------------------------------------
    cookie_domain: str = "localhost"
    cookie_secure: bool = False
    #: Public path the refresh cookie is scoped to. Must match the gateway's
    #: auth prefix as the browser sees it — see `cookies.py`.
    refresh_cookie_path: str = "/api/auth"

    # --- Brute-force protection ---------------------------------------------
    #: Failed logins tolerated per email within the window before lockout.
    max_login_attempts: int = 10
    #: Window and lockout duration, in seconds.
    login_attempt_window_seconds: int = 900
    #: How long a password-reset link stays valid. Short on purpose: the link
    #: sits in an inbox, which is exactly where an attacker with stale access
    #: would look.
    password_reset_ttl_seconds: int = 3600

    #: How long an email verification link stays valid.
    #:
    #: Days rather than an hour. Unlike a password reset, nobody is waiting at
    #: the keyboard for this one - people sign up, get distracted, and come
    #: back to their inbox the next morning. A link that has expired by then
    #: just creates a support ticket.
    email_verification_ttl_seconds: int = 604_800  # 7 days

    #: Block sign-in until the address is verified.
    #:
    #: Off by default, and that is a deliberate trade rather than an oversight.
    #: Turning it on means a customer who mistypes their address, or whose
    #: provider silently bins the message, is locked out of an account they
    #: already paid with - and you only find out when they complain. With it
    #: off, verification still happens, it is still recorded, and the admin
    #: console still shows who has and has not confirmed.
    require_verified_email: bool = False

    # --- Outbound -----------------------------------------------------------
    notifications_url: str = "http://notifications:8000"

    @field_validator("admin_emails")
    @classmethod
    def _normalise_admin_emails(cls, value: str) -> str:
        """Lowercase and strip, so ``Me@X.com `` matches ``me@x.com``."""
        return ",".join(part.strip().lower() for part in value.split(",") if part.strip())

    @property
    def admin_email_set(self) -> frozenset[str]:
        """The admin allowlist as a set of lowercase addresses."""
        return frozenset(e for e in self.admin_emails.split(",") if e)

    def is_admin_email(self, email: str) -> bool:
        """Whether `email` is permitted to hold the admin role.

        Args:
            email: Address to check, any casing.

        Returns:
            ``True`` if the address is on the allowlist.
        """
        return email.strip().lower() in self.admin_email_set
