"""Payment business logic: intents, webhook handling and refunds."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from ecom_shared.errors import ConflictError, NotFoundError, UpstreamError, ValidationFailedError
from ecom_shared.logging import get_logger, get_request_id
from ecom_shared.middleware import REQUEST_ID_HEADER
from ecom_shared.security import create_service_token
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ecom_payments.config import PaymentSettings
from ecom_payments.models import Payment, Refund, WebhookEvent
from ecom_payments.stripe_gateway import StripeGateway

log = get_logger(__name__)

#: Stripe event types this service acts on. Anything else is recorded and
#: acknowledged without processing — Stripe sends a great many event types, and
#: silently ignoring the ones we do not handle is correct, while erroring on
#: them would make Stripe retry them forever.
HANDLED_EVENTS = frozenset(
    {
        "payment_intent.succeeded",
        "payment_intent.payment_failed",
        "payment_intent.canceled",
        "charge.refunded",
    }
)


async def create_payment_intent(
    session: AsyncSession,
    gateway: StripeGateway,
    *,
    order_id: UUID,
    order_number: str,
    amount_cents: int,
    currency: str,
    email: str,
) -> Payment:
    """Create (or return an existing) payment intent for an order.

    Idempotent in two layers, because a checkout that is retried after a
    timeout must not produce two charges:

    1. **Ours.** If a pending payment already exists for this order, it is
       returned rather than a second intent being created.
    2. **Stripe's.** The order ID is used as the idempotency key, so even if
       our check races, Stripe returns the original intent.

    Args:
        session: Active session.
        gateway: Stripe wrapper.
        order_id: The order being paid for.
        order_number: Human-readable reference.
        amount_cents: Amount computed by the orders service.
        currency: ISO 4217, lowercase.
        email: Receipt address.

    Returns:
        The payment record, with `payment_intent_id` populated.
    """
    existing = (
        await session.execute(
            select(Payment).where(Payment.order_id == order_id, Payment.status == "pending")
        )
    ).scalar_one_or_none()
    if existing is not None:
        log.info("payment_intent_reused", order_id=str(order_id))
        return existing

    intent = await gateway.create_payment_intent(
        amount_cents=amount_cents,
        currency=currency,
        order_id=str(order_id),
        order_number=order_number,
        email=email,
        # Derived from the order, so a retry for the same order deduplicates
        # at Stripe even if our own check above missed.
        idempotency_key=f"order-{order_id}",
    )

    payment = Payment(
        order_id=order_id,
        order_number=order_number,
        payment_intent_id=intent["id"],
        status="pending",
        stripe_status=intent.get("status"),
        amount_cents=amount_cents,
        currency=currency.lower(),
        email=email,
    )
    session.add(payment)
    await session.flush()

    # Stash the client secret on the instance for the response. Never stored:
    # it is a transient credential for one browser session and there is no
    # reason for it to outlive the request.
    payment.client_secret = intent["client_secret"]  # type: ignore[attr-defined]

    log.info(
        "payment_intent_created",
        order_id=str(order_id),
        payment_intent_id=intent["id"],
        amount_cents=amount_cents,
    )
    return payment


async def record_webhook_event(session: AsyncSession, event: dict[str, Any]) -> WebhookEvent | None:
    """Persist a verified webhook, returning ``None`` if already seen.

    The duplicate check is the unique constraint on `stripe_event_id`, not a
    prior SELECT. Two deliveries of the same event arriving concurrently would
    both pass a check-then-insert; the constraint cannot be raced.

    Args:
        session: Active session.
        event: The verified Stripe event.

    Returns:
        The stored row, or ``None`` if this event was already processed.
    """
    record = WebhookEvent(
        stripe_event_id=event["id"],
        event_type=event["type"],
        payload=event,
    )
    session.add(record)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        log.info("webhook_duplicate_ignored", stripe_event_id=event["id"])
        return None
    return record


async def handle_payment_succeeded(
    session: AsyncSession,
    settings: PaymentSettings,
    intent: dict[str, Any],
) -> None:
    """Mark a payment succeeded and tell the orders service.

    Args:
        session: Active session.
        settings: Supplies the orders URL and signing key.
        intent: The `payment_intent` object from the event.

    Raises:
        UpstreamError: If the orders service cannot be reached. Raising here is
            deliberate: the webhook then returns non-2xx, Stripe retries, and
            the order eventually gets marked paid. Swallowing the error would
            leave a customer charged with an order stuck in pending_payment.
    """
    payment = (
        await session.execute(select(Payment).where(Payment.payment_intent_id == intent["id"]))
    ).scalar_one_or_none()

    if payment is None:
        # An intent we have no record of. Possible if the database was restored
        # from a backup, or if someone is replaying an event from another
        # account. Log and acknowledge rather than retrying forever.
        log.error("webhook_unknown_payment_intent", payment_intent_id=intent["id"])
        return

    if payment.status == "succeeded":
        log.info("payment_already_succeeded", payment_intent_id=intent["id"])
        return

    amount_received = int(intent.get("amount_received") or 0)
    payment.status = "succeeded"
    payment.stripe_status = intent.get("status")
    payment.amount_received_cents = amount_received
    payment.succeeded_at = datetime.now(UTC)
    await session.flush()

    await _notify_orders(
        settings,
        path="/internal/orders/payments/succeeded",
        payload={
            "paymentIntentId": payment.payment_intent_id,
            "amountReceivedCents": amount_received,
        },
    )
    log.info(
        "payment_succeeded",
        payment_intent_id=intent["id"],
        order_number=payment.order_number,
        amount_cents=amount_received,
    )


async def handle_payment_failed(
    session: AsyncSession,
    settings: PaymentSettings,
    intent: dict[str, Any],
    *,
    cancelled: bool = False,
) -> None:
    """Record a failed or cancelled payment and tell the orders service.

    Args:
        session: Active session.
        settings: Supplies the orders URL and signing key.
        intent: The `payment_intent` object from the event.
        cancelled: Whether this was a cancellation rather than a failure.
    """
    payment = (
        await session.execute(select(Payment).where(Payment.payment_intent_id == intent["id"]))
    ).scalar_one_or_none()
    if payment is None:
        log.error("webhook_unknown_payment_intent", payment_intent_id=intent["id"])
        return

    error = intent.get("last_payment_error") or {}
    payment.status = "cancelled" if cancelled else "failed"
    payment.stripe_status = intent.get("status")
    payment.failure_code = error.get("code")
    # Stripe's customer-facing decline messages are written to be shown to the
    # shopper, so this one is safe to surface.
    payment.failure_message = error.get("message")
    await session.flush()

    await _notify_orders(
        settings,
        path="/internal/orders/payments/failed",
        payload={
            "paymentIntentId": payment.payment_intent_id,
            "reason": payment.failure_message or ("Cancelled" if cancelled else "Failed"),
        },
    )
    log.info(
        "payment_failed",
        payment_intent_id=intent["id"],
        order_number=payment.order_number,
        code=payment.failure_code,
    )


async def _notify_orders(settings: PaymentSettings, *, path: str, payload: dict[str, Any]) -> None:
    """POST to an internal orders endpoint with a service token.

    Args:
        settings: Supplies the orders URL and the signing key.
        path: Internal route path.
        payload: JSON body.

    Raises:
        UpstreamError: If orders is unreachable or errors. Propagated so the
            webhook returns non-2xx and Stripe retries.
    """
    token = create_service_token(
        service_name="payments",
        secret_key=settings.jwt_secret_key.get_secret_value(),
        ttl_seconds=60,
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=2.0)) as client:
            response = await client.post(
                f"{settings.orders_url}{path}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    REQUEST_ID_HEADER: get_request_id(),
                },
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("orders_notification_failed", path=path, error=str(exc))
        raise UpstreamError("Could not reach the orders service.") from exc


async def refund_payment(
    session: AsyncSession,
    gateway: StripeGateway,
    *,
    payment_id: UUID,
    amount_cents: int | None,
    reason: str | None,
    actor_user_id: UUID,
) -> Refund:
    """Refund a payment through Stripe and record it.

    Args:
        session: Active session.
        gateway: Stripe wrapper.
        payment_id: Which payment to refund.
        amount_cents: Partial amount, or ``None`` for the full capture.
        reason: Free text recorded on the refund.
        actor_user_id: The admin issuing it. Refunds move real money, so they
            are always attributable to a person.

    Returns:
        The recorded refund.

    Raises:
        NotFoundError: If the payment does not exist.
        ConflictError: If the payment never succeeded.
        ValidationFailedError: If the amount exceeds what remains refundable.
    """
    payment = await session.get(Payment, payment_id)
    if payment is None:
        raise NotFoundError("Payment not found.")
    if payment.status not in ("succeeded", "refunded"):
        raise ConflictError("Only a successful payment can be refunded.")

    await session.refresh(payment, ["refunds"])
    already_refunded = sum(r.amount_cents for r in payment.refunds)
    refundable = payment.amount_received_cents - already_refunded

    if refundable <= 0:
        raise ConflictError("This payment has already been refunded in full.")

    requested = amount_cents if amount_cents is not None else refundable
    if requested > refundable:
        raise ValidationFailedError(
            f"At most {refundable} cents can still be refunded on this payment.",
            details={"refundableCents": refundable, "requestedCents": requested},
        )

    stripe_refund = await gateway.refund(
        payment_intent_id=payment.payment_intent_id,
        amount_cents=requested,
        reason=reason,
    )

    refund = Refund(
        payment_id=payment.id,
        stripe_refund_id=stripe_refund["id"],
        amount_cents=requested,
        reason=reason,
        actor_user_id=actor_user_id,
    )
    session.add(refund)

    if requested == refundable:
        payment.status = "refunded"

    await session.flush()
    log.info(
        "refund_issued",
        payment_id=str(payment.id),
        amount_cents=requested,
        actor=str(actor_user_id),
        full=requested == refundable,
    )
    return refund
