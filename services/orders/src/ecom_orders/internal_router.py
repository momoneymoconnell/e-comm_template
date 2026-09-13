"""Service-to-service routes for the orders service.

Only the payments service calls these, and only after it has verified a Stripe
webhook signature. Marking an order paid is the single most security-sensitive
operation in the system — it is the difference between shipping goods and
giving them away — so it is reachable only here, behind a service token, on a
path the gateway does not proxy.
"""

from __future__ import annotations

from ecom_shared.identity import ServiceCaller
from ecom_shared.logging import get_logger
from ecom_shared.schemas import Message
from fastapi import APIRouter

from ecom_orders import service
from ecom_orders.deps import Catalog, Db, Notifications
from ecom_orders.schemas import (
    MarkPaidRequest,
    OrderResponse,
    PaymentFailedRequest,
    PurchaseCheckRequest,
    PurchaseCheckResponse,
)

log = get_logger(__name__)

router = APIRouter(prefix="/internal/orders", tags=["internal"])


@router.post(
    "/payments/succeeded",
    response_model=OrderResponse,
    summary="Record a confirmed payment",
)
async def payment_succeeded(
    payload: MarkPaidRequest,
    caller: ServiceCaller,
    db: Db,
    notifications: Notifications,
) -> OrderResponse:
    """Mark the order behind a payment intent as paid.

    Idempotent, because Stripe delivers webhooks at least once and retries
    anything that is not a 2xx. Calling this twice for the same payment returns
    the already-paid order without re-running the transition or sending a
    second receipt.

    The captured amount is compared against the order total; a mismatch is
    rejected rather than fulfilled.
    """
    order = await service.mark_order_paid(
        db,
        payment_intent_id=payload.payment_intent_id,
        amount_received_cents=payload.amount_received_cents,
        notifications=notifications,
    )
    log.info("payment_succeeded_processed", caller=caller, order_id=str(order.id))
    return OrderResponse.model_validate(order)


@router.post("/payments/failed", response_model=Message, summary="Record a failed payment")
async def payment_failed(
    payload: PaymentFailedRequest,
    caller: ServiceCaller,
    db: Db,
    catalog: Catalog,
) -> Message:
    """Cancel the order behind a failed payment and release its stock.

    Always answers 200, even when no matching order is found or it has already
    moved on. A non-2xx would make Stripe retry a webhook that can never
    succeed, indefinitely.
    """
    order = await service.mark_payment_failed(
        db,
        payment_intent_id=payload.payment_intent_id,
        reason=payload.reason,
        catalog=catalog,
    )
    if order is None:
        log.info("payment_failed_no_action", caller=caller, intent=payload.payment_intent_id)
        return Message(message="No action taken.")
    return Message(message=f"Order {order.order_number} cancelled.")


@router.post(
    "/purchases/check",
    response_model=PurchaseCheckResponse,
    summary="Has this customer bought any of these variants?",
)
async def check_purchase(
    payload: PurchaseCheckRequest, caller: ServiceCaller, db: Db
) -> PurchaseCheckResponse:
    """Report whether the customer has a paid order containing any variant.

    Only orders that actually reached a paid state count. A pending order
    proves nothing - anyone can start a checkout - and a cancelled one proves
    less than nothing.
    """
    from sqlalchemy import select

    from ecom_orders.models import Order, OrderItem

    result = await db.execute(
        select(Order.paid_at)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .where(
            Order.user_id == payload.user_id,
            Order.status.in_(("paid", "fulfilled", "delivered")),
            OrderItem.variant_id.in_(payload.variant_ids),
        )
        .order_by(Order.paid_at)
        .limit(1)
    )
    first = result.scalar_one_or_none()
    log.info("purchase_check", caller=caller, purchased=first is not None)
    return PurchaseCheckResponse(purchased=first is not None, first_purchased_at=first)
