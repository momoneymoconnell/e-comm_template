"""Request and response models for the payments API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from ecom_shared.schemas import ApiModel
from pydantic import EmailStr, Field


class CreateIntentRequest(ApiModel):
    """Internal: orders asking for a payment intent.

    The amount comes from the orders service, which computed it from the
    catalogue. It is never supplied by a browser.
    """

    order_id: UUID
    order_number: str = Field(min_length=1, max_length=32)
    amount_cents: int = Field(gt=0, le=100_000_000, description="Amount in minor units.")
    currency: str = Field(min_length=3, max_length=3)
    email: EmailStr


class CreateIntentResponse(ApiModel):
    """The identifiers the browser needs to complete a payment.

    Attributes:
        client_secret: Scoped to this one payment intent. Safe to send to the
            browser — it permits confirming *this* payment and nothing else. It
            is not an API key and grants no access to the Stripe account.
    """

    payment_intent_id: str
    client_secret: str
    amount_cents: int
    currency: str


class PaymentResponse(ApiModel):
    """A payment record, for the admin console."""

    id: UUID
    order_id: UUID
    order_number: str
    payment_intent_id: str
    status: str
    stripe_status: str | None
    amount_cents: int
    amount_received_cents: int
    currency: str
    email: str | None
    failure_message: str | None
    succeeded_at: datetime | None
    created_at: datetime


class RefundRequest(ApiModel):
    """Admin: refund a payment, in whole or in part."""

    amount_cents: int | None = Field(
        default=None, gt=0, description="Omit to refund the full captured amount."
    )
    reason: str | None = Field(default=None, max_length=200)


class RefundResponse(ApiModel):
    """A completed refund."""

    id: UUID
    payment_id: UUID
    stripe_refund_id: str
    amount_cents: int
    reason: str | None
    created_at: datetime
