"""Database tables for outbound email (schema ``notifications``).

**The outbox pattern.** A request to send an email writes a row and returns
immediately; a background worker does the actual SMTP conversation.

The alternative — sending inline, during the request — fails badly in exactly
the situation that matters most. If the mail server is slow, checkout is slow.
If it is down, checkout *fails*, and a customer who has already been charged
sees an error. Decoupling means a mail outage delays receipts and nothing else,
and the queued messages go out when it recovers.

It also gives retries somewhere to live: the row records how many attempts have
been made and what went wrong last time, so a transient failure is recoverable
without anyone noticing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from ecom_shared.db import build_metadata, utcnow_sql
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for the notifications service.

    Its metadata carries schema="notifications", so every table declared
    against this base is created inside that schema.
    """

    metadata = build_metadata("notifications")


#: Delivery lifecycle.
#:
#: ``queued``   — waiting for the worker.
#: ``sending``  — claimed by a worker; prevents two workers sending it twice.
#: ``sent``     — accepted by the SMTP server.
#: ``failed``   — permanently given up after `max_attempts`.
NOTIFICATION_STATUSES = ("queued", "sending", "sent", "failed")


class Notification(Base):
    """One outbound message.

    Attributes:
        template: Which template rendered it, e.g. ``"order_confirmation"``.
        context: The variables it was rendered with. Retained so a message can
            be re-rendered after a template fix, and so "what exactly did we
            send this customer" is answerable during a support conversation.
        attempts: Delivery attempts so far. Drives the retry backoff.
        next_attempt_at: When the worker may next try. Backoff is exponential,
            so a mail server having a bad minute is not hammered by every
            queued message at once.
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    template: Mapped[str] = mapped_column(String(80), nullable=False)
    to_email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)

    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )

    __table_args__ = (
        CheckConstraint(f"status IN {NOTIFICATION_STATUSES}", name="status_valid"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        # The worker's only query is "queued messages whose time has come",
        # ordered oldest first. This index answers it with one range scan.
        Index("ix_notifications_pending", "status", "next_attempt_at"),
        Index("ix_notifications_created", "created_at"),
    )
