"""Configuration for the notifications service."""

from __future__ import annotations

from ecom_shared.config import ServiceSettings
from pydantic import SecretStr


class NotificationSettings(ServiceSettings):
    """Settings for transactional email delivery.

    Attributes:
        smtp_host: SMTP server. Defaults to Mailpit in development, which
            captures every message at http://localhost:8025 and delivers none
            of them — so testing a signup flow cannot accidentally email a real
            person.
        outbox_poll_seconds: How often the background worker looks for queued
            mail.
        max_attempts: Delivery attempts before a message is given up on.
    """

    service_name: str = "notifications"
    db_schema: str = "notifications"

    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_use_tls: bool = False

    email_from: str = "no-reply@localhost"
    email_from_name: str = "Atelier"

    outbox_poll_seconds: int = 5
    max_attempts: int = 5
    #: Messages older than this are purged. Transactional email has no reason
    #: to be retained indefinitely, and a table of everyone's addresses and
    #: order details is a liability that grows every day you keep it.
    retention_days: int = 90
