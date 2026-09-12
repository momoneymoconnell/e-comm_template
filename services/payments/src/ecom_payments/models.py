"""Database tables for payments (schema ``payments``).

**What is deliberately absent from this schema: card data.** There is no column
for a card number, expiry, CVC or cardholder name, and there never should be.
Card details go from the customer's browser directly to Stripe via Stripe
Elements; our servers only ever see an opaque `payment_intent_id`.

That single design decision is what keeps this system out of PCI-DSS scope.
Storing a primary account number — even encrypted — pulls you into a compliance
regime with annual audits and network segmentation requirements. There is no
good reason to enter it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from ecom_shared.db import declarative_base_for, utcnow_sql
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

Base = declarative_base_for("payments")

#: Our own view of a payment's progress, kept separate from Stripe's status
#: vocabulary so a change on their side does not ripple through our logic.
PAYMENT_STATUSES = ("pending", "succeeded", "failed", "cancelled", "refunded")


class Payment(Base):
    """One attempt to collect money for an order.

    Attributes:
        order_id: The order in the orders service. Not a foreign key — that
            table lives in a different schema this role cannot see, which is
            the isolation working as intended. Referential integrity across
            services is maintained by the services, not the database.
        payment_intent_id: Stripe's identifier. Unique, which makes it a
            natural idempotency key for webhook handling.
        amount_cents: What we asked Stripe to charge.
        amount_received_cents: What Stripe reports it actually captured. Stored
            separately and compared, because a discrepancy means either a bug
            in our own intent creation or something worth investigating.
    """

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    order_number: Mapped[str] = mapped_column(String(32), nullable=False)

    payment_intent_id: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    #: Stripe's own status string, kept verbatim for debugging against their
    #: dashboard without having to reverse our mapping.
    stripe_status: Mapped[str | None] = mapped_column(String(40))

    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_received_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="usd")
    email: Mapped[str | None] = mapped_column(CITEXT)

    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_message: Mapped[str | None] = mapped_column(Text)

    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql(), onupdate=func.now()
    )

    refunds: Mapped[list[Refund]] = relationship(
        back_populates="payment", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(f"status IN {PAYMENT_STATUSES}", name="status_valid"),
        CheckConstraint("amount_cents > 0", name="amount_positive"),
        Index("ix_payments_status_created", "status", "created_at"),
    )


class Refund(Base):
    """Money returned to a customer.

    Attributes:
        amount_cents: Partial refunds are supported, so this is not necessarily
            the full payment.
    """

    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id", ondelete="CASCADE"), nullable=False
    )
    stripe_refund_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))
    #: Which admin issued it. Refunds move real money, so they are always
    #: attributable to a person.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )

    payment: Mapped[Payment] = relationship(back_populates="refunds")

    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="amount_positive"),
        Index("ix_refunds_payment", "payment_id"),
    )


class WebhookEvent(Base):
    """A Stripe webhook we have received, recorded for idempotency and audit.

    Stripe guarantees *at-least-once* delivery and retries anything that does
    not return 2xx — for up to three days. The same event will therefore arrive
    more than once, and without protection a duplicate "payment succeeded"
    could fulfil an order twice or send two receipts.

    The unique constraint on `stripe_event_id` is what prevents that: the
    second insert fails, the handler recognises the collision, and returns 200
    without reprocessing. The database provides the guarantee, so two webhook
    deliveries arriving concurrently cannot both slip through.

    The raw payload is retained because when a payment is disputed weeks later,
    exactly what Stripe sent and when is the record that settles it.
    """

    __tablename__ = "webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    stripe_event_id: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")

    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )

    __table_args__ = (Index("ix_webhook_events_type_time", "event_type", "received_at"),)
