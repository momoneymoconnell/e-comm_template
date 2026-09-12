"""Payment routes: the Stripe webhook, internal intent creation, admin refunds.

The webhook is the only public endpoint, and it is public because Stripe has to
be able to reach it. Its safety rests entirely on signature verification — see
`StripeGateway.verify_webhook`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from ecom_shared.identity import AdminUser, ServiceCaller, require_admin
from ecom_shared.logging import get_logger
from ecom_shared.schemas import Message, Page, PageParams
from fastapi import APIRouter, Depends, Header, Query, Request, status
from sqlalchemy import func, select

from ecom_payments import service
from ecom_payments.deps import Db, Gateway, Settings
from ecom_payments.models import Payment, Refund
from ecom_payments.schemas import (
    CreateIntentRequest,
    CreateIntentResponse,
    PaymentResponse,
    RefundRequest,
    RefundResponse,
)
from ecom_payments.service import HANDLED_EVENTS

log = get_logger(__name__)

# --- Public: the Stripe webhook ----------------------------------------------
webhook_router = APIRouter(prefix="/payments", tags=["payments"])

# --- Internal: called by orders ----------------------------------------------
internal_router = APIRouter(prefix="/internal/payments", tags=["internal"])

# --- Admin --------------------------------------------------------------------
admin_router = APIRouter(
    prefix="/payments/admin",
    tags=["payments-admin"],
    dependencies=[Depends(require_admin)],
)

PageQuery = Annotated[PageParams, Depends()]


@webhook_router.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    summary="Stripe webhook receiver",
    # Hidden from the public schema: it is not an API for anyone to call, and
    # documenting it only helps someone probing for it.
    include_in_schema=False,
)
async def stripe_webhook(
    request: Request,
    db: Db,
    settings: Settings,
    gateway: Gateway,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> dict[str, str]:
    """Receive, verify and process a Stripe event.

    Order of operations matters here:

    1. **Read the raw body.** The signature covers the exact bytes Stripe sent.
       Letting FastAPI parse and re-serialise the JSON first would change key
       order and whitespace and break verification.
    2. **Verify the signature.** Nothing else happens until this passes. An
       unverified request is rejected with 400 and no business logic runs — the
       endpoint is public, so this check is the only thing standing between an
       attacker and free merchandise.
    3. **Deduplicate.** Stripe retries anything non-2xx for up to three days,
       so the same event will arrive more than once.
    4. **Process**, and return 200 so Stripe stops retrying.

    A processing failure deliberately returns non-2xx so Stripe *does* retry:
    if the orders service is briefly down, the order still gets marked paid a
    minute later rather than leaving a charged customer with a pending order.
    """
    raw_body = await request.body()

    if not stripe_signature:
        log.warning("webhook_missing_signature")
        return {"status": "rejected"}

    event = gateway.verify_webhook(raw_body, stripe_signature)

    record = await service.record_webhook_event(db, event)
    if record is None:
        return {"status": "duplicate"}

    event_type = event["type"]
    if event_type not in HANDLED_EVENTS:
        # Stripe emits dozens of event types. Acknowledging the ones we do not
        # act on stops them being retried indefinitely.
        log.info("webhook_ignored", event_type=event_type)
        return {"status": "ignored"}

    intent = event["data"]["object"]

    if event_type == "payment_intent.succeeded":
        await service.handle_payment_succeeded(db, settings, intent)
    elif event_type == "payment_intent.payment_failed":
        await service.handle_payment_failed(db, settings, intent)
    elif event_type == "payment_intent.canceled":
        await service.handle_payment_failed(db, settings, intent, cancelled=True)
    elif event_type == "charge.refunded":
        log.info("webhook_charge_refunded", charge_id=intent.get("id"))

    from datetime import UTC, datetime

    record.processed_at = datetime.now(UTC)
    return {"status": "processed"}


@internal_router.post(
    "/intents",
    response_model=CreateIntentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a payment intent for an order",
)
async def create_intent(
    payload: CreateIntentRequest, caller: ServiceCaller, db: Db, gateway: Gateway
) -> CreateIntentResponse:
    """Create a Stripe payment intent. Called only by the orders service.

    The amount comes from orders, which computed it from the catalogue. If a
    browser could reach this endpoint it could name its own price, which is why
    it sits behind a service token on a path the gateway does not proxy.
    """
    payment = await service.create_payment_intent(
        db,
        gateway,
        order_id=payload.order_id,
        order_number=payload.order_number,
        amount_cents=payload.amount_cents,
        currency=payload.currency,
        email=payload.email,
    )
    log.info("intent_requested", caller=caller, order_number=payload.order_number)
    return CreateIntentResponse(
        payment_intent_id=payment.payment_intent_id,
        client_secret=getattr(payment, "client_secret", ""),
        amount_cents=payment.amount_cents,
        currency=payment.currency,
    )


@admin_router.get("", response_model=Page[PaymentResponse], summary="List payments")
async def list_payments(
    admin: AdminUser,
    db: Db,
    params: PageQuery,
    payment_status: Annotated[
        str | None, Query(alias="status", pattern="^(pending|succeeded|failed|cancelled|refunded)$")
    ] = None,
) -> Page[PaymentResponse]:
    """Return a page of payments, newest first."""
    conditions = []
    if payment_status:
        conditions.append(Payment.status == payment_status)

    total = await db.scalar(select(func.count()).select_from(Payment).where(*conditions))
    result = await db.execute(
        select(Payment)
        .where(*conditions)
        .order_by(Payment.created_at.desc())
        .offset(params.offset)
        .limit(params.limit)
    )
    items = [PaymentResponse.model_validate(p) for p in result.scalars().all()]
    return Page[PaymentResponse].build(items, int(total or 0), params)


@admin_router.post(
    "/{payment_id}/refund", response_model=RefundResponse, summary="Refund a payment"
)
async def refund(
    payment_id: UUID, payload: RefundRequest, admin: AdminUser, db: Db, gateway: Gateway
) -> RefundResponse:
    """Refund a payment in whole or in part.

    Attributed to the acting admin, because this moves real money out of the
    business and "who authorised this" must always be answerable.
    """
    record = await service.refund_payment(
        db,
        gateway,
        payment_id=payment_id,
        amount_cents=payload.amount_cents,
        reason=payload.reason,
        actor_user_id=admin.user_id,
    )
    return RefundResponse.model_validate(record)


@admin_router.get("/stats", summary="Payment statistics")
async def payment_stats(admin: AdminUser, db: Db) -> dict[str, int]:
    """Return headline payment figures for the dashboard."""
    row = (
        await db.execute(
            select(
                func.count().label("total"),
                func.count().filter(Payment.status == "succeeded").label("succeeded"),
                func.count().filter(Payment.status == "failed").label("failed"),
                func.coalesce(
                    func.sum(Payment.amount_received_cents).filter(
                        Payment.status.in_(("succeeded", "refunded"))
                    ),
                    0,
                ).label("captured_cents"),
            ).select_from(Payment)
        )
    ).one()
    refunded = await db.scalar(select(func.coalesce(func.sum(Refund.amount_cents), 0)))

    return {
        "totalPayments": int(row.total),
        "succeeded": int(row.succeeded),
        "failed": int(row.failed),
        "capturedCents": int(row.captured_cents),
        "refundedCents": int(refunded or 0),
        "netCents": int(row.captured_cents) - int(refunded or 0),
    }


@admin_router.get("/health/stripe", response_model=Message, summary="Stripe configuration")
async def stripe_health(admin: AdminUser, settings: Settings) -> Message:
    """Report whether Stripe credentials are configured.

    Reports configuration only — it never echoes the key itself.
    """
    if not settings.stripe_configured:
        return Message(message="Stripe is not configured. Set STRIPE_SECRET_KEY in .env.")
    mode = "live" if settings.stripe_secret_key.get_secret_value().startswith("sk_live") else "test"
    return Message(message=f"Stripe configured in {mode} mode.")
