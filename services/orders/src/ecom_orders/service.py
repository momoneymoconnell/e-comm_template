"""Cart and order business logic.

The important flows, and the invariants each one protects:

* `price_cart` — the cart is repriced from the catalogue on every read, so a
  cart left open overnight cannot be checked out at yesterday's price.
* `checkout` — reserve stock, then create the order, then create the payment
  intent, with compensation if any step fails. This is where money and
  inventory meet, so it is documented step by step below.
* `transition_order` — every status change goes through one function that
  consults `ALLOWED_TRANSITIONS`, so an order can never reach an impossible
  state (refunded then fulfilled, cancelled then paid).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import UUID

from ecom_shared.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailedError
from ecom_shared.logging import get_logger
from ecom_shared.security import hash_token
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ecom_orders.clients import CatalogClient, NotificationsClient, PaymentsClient
from ecom_orders.config import OrderSettings
from ecom_orders.models import (
    ALLOWED_TRANSITIONS,
    Cart,
    CartItem,
    Order,
    OrderEvent,
    OrderItem,
)

log = get_logger(__name__)

#: Characters used in the human-readable part of an order number.
#:
#: Excludes I, O, 0, 1 — the glyphs people reliably misread when copying a
#: reference off a screen or reading it down a phone line.
ORDER_NUMBER_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_order_number() -> str:
    """Build a human-readable order reference, e.g. ``ORD-20260912-K3F9QX``.

    The date prefix makes orders sortable and instantly recognisable in a
    support conversation; the random suffix is what makes them unguessable.

    Guessability matters: a sequential number leaks your daily order volume to
    any competitor who places two orders, and it invites someone to try
    neighbouring numbers to look at other people's orders. Authorisation is
    still checked on every read — this just removes the temptation to try.

    Returns:
        A unique-with-overwhelming-probability order number. A database unique
        constraint is the actual guarantee.
    """
    today = datetime.now(UTC).strftime("%Y%m%d")
    suffix = "".join(secrets.choice(ORDER_NUMBER_ALPHABET) for _ in range(6))
    return f"ORD-{today}-{suffix}"


# -----------------------------------------------------------------------------
# Cart retrieval
# -----------------------------------------------------------------------------


async def get_or_create_cart(
    session: AsyncSession,
    *,
    user_id: UUID | None,
    guest_token: str | None,
) -> Cart:
    """Find the caller's open cart, creating one if needed.

    Resolution order, and why:

    1. **Signed in** — look up by `user_id`, so the cart follows the person
       between their phone and their laptop.
    2. **Guest** — look up by the hash of the token in their cookie.
    3. **Neither** — create a new guest cart.

    Args:
        session: Active session.
        user_id: The signed-in user, if any.
        guest_token: The opaque token from the guest's cart cookie, if any.

    Returns:
        An open cart with its items loaded.
    """
    if user_id is not None:
        result = await session.execute(
            select(Cart)
            .where(Cart.user_id == user_id, Cart.status == "open")
            .options(selectinload(Cart.items))
            .order_by(Cart.created_at.desc())
        )
        if (cart := result.scalars().first()) is not None:
            return cart

    if guest_token:
        result = await session.execute(
            select(Cart)
            .where(
                Cart.session_token_hash == hash_token(guest_token),
                Cart.status == "open",
            )
            .options(selectinload(Cart.items))
        )
        if (cart := result.scalar_one_or_none()) is not None:
            # The shopper signed in with items already in a guest cart. Claim
            # it rather than stranding it, so nothing is lost at the login step
            # — the single most common place a checkout is abandoned.
            if user_id is not None and cart.user_id is None:
                cart.user_id = user_id
                log.info("guest_cart_claimed", cart_id=str(cart.id), user_id=str(user_id))
            return cart

    cart = Cart(
        user_id=user_id,
        session_token_hash=hash_token(guest_token) if guest_token else None,
    )
    session.add(cart)
    await session.flush()
    await session.refresh(cart, ["items"])
    return cart


async def add_to_cart(
    session: AsyncSession, cart: Cart, *, variant_id: UUID, quantity: int
) -> Cart:
    """Add a variant, or increase its quantity if already present.

    Args:
        session: Active session.
        cart: The cart to modify.
        variant_id: The variant to add.
        quantity: How many to add.

    Returns:
        The updated cart.

    Raises:
        ValidationFailedError: If the resulting quantity would exceed the
            per-line cap.
    """
    for item in cart.items:
        if item.variant_id == variant_id:
            new_quantity = item.quantity + quantity
            if new_quantity > 100:
                raise ValidationFailedError("You can order at most 100 of one item.")
            item.quantity = new_quantity
            await session.flush()
            return cart

    cart.items.append(CartItem(variant_id=variant_id, quantity=quantity))
    await session.flush()
    await session.refresh(cart, ["items"])
    return cart


async def set_cart_item_quantity(
    session: AsyncSession, cart: Cart, *, item_id: UUID, quantity: int
) -> Cart:
    """Set a line's quantity, removing the line when it reaches zero.

    Args:
        session: Active session.
        cart: The cart to modify.
        item_id: The line to change.
        quantity: New quantity; ``0`` removes the line.

    Returns:
        The updated cart.

    Raises:
        NotFoundError: If the line is not in this cart. The cart-scoped lookup
            is the authorisation check — a line belonging to someone else's
            cart simply does not match.
    """
    target = next((i for i in cart.items if i.id == item_id), None)
    if target is None:
        raise NotFoundError("That item is not in your cart.")

    if quantity == 0:
        cart.items.remove(target)
        await session.delete(target)
    else:
        target.quantity = quantity

    await session.flush()
    await session.refresh(cart, ["items"])
    return cart


# -----------------------------------------------------------------------------
# Pricing
# -----------------------------------------------------------------------------


def compute_totals(subtotal_cents: int, settings: OrderSettings) -> tuple[int, int, int]:
    """Derive tax, shipping and grand total from a subtotal.

    All arithmetic is on integers. `//` after multiplying by the rate keeps the
    result exact and rounds down consistently — a float rate would introduce
    fractions of a cent that eventually surface as a total that does not match
    the sum of its lines.

    Args:
        subtotal_cents: Sum of the line totals.
        settings: Supplies the tax rate and shipping rules.

    Returns:
        ``(tax_cents, shipping_cents, total_cents)``.
    """
    tax_cents = (subtotal_cents * settings.tax_rate_basis_points) // 10_000

    if subtotal_cents == 0:
        shipping_cents = 0
    elif (
        settings.free_shipping_threshold_cents
        and subtotal_cents >= settings.free_shipping_threshold_cents
    ):
        shipping_cents = 0
    else:
        shipping_cents = settings.flat_shipping_cents

    return tax_cents, shipping_cents, subtotal_cents + tax_cents + shipping_cents


async def price_cart(cart: Cart, catalog: CatalogClient, settings: OrderSettings) -> dict:
    """Price a cart against the live catalogue.

    Called on every cart read and again at checkout. Prices are never cached on
    the cart, so what the shopper sees is always current.

    Lines whose variant has been deactivated or deleted are marked unavailable
    and excluded from the total, rather than dropped. A line that silently
    vanishes looks like a bug to the shopper; a line marked "no longer
    available" is self-explanatory.

    Args:
        cart: The cart to price.
        catalog: Client for the authoritative price lookup.
        settings: Supplies tax and shipping rules.

    Returns:
        A dict matching `CartResponse`.
    """
    if not cart.items:
        return {
            "id": cart.id,
            "items": [],
            "subtotal_cents": 0,
            "tax_cents": 0,
            "shipping_cents": 0,
            "total_cents": 0,
            "currency": cart.currency,
            "item_count": 0,
            "has_unavailable_items": False,
        }

    priced = await catalog.price_variants([item.variant_id for item in cart.items])
    by_id = {UUID(entry["id"]): entry for entry in priced}

    items: list[dict] = []
    subtotal = 0
    any_unavailable = False

    for item in cart.items:
        entry = by_id.get(item.variant_id)
        if entry is None:
            any_unavailable = True
            items.append(
                {
                    "id": item.id,
                    "variant_id": item.variant_id,
                    "sku": "—",
                    "product_title": "No longer available",
                    "product_slug": None,
                    "variant_name": "",
                    "image_url": None,
                    "unit_price_cents": 0,
                    "quantity": item.quantity,
                    "total_cents": 0,
                    "available": False,
                }
            )
            continue

        has_stock = entry["isActive"] and (
            not entry["trackInventory"] or entry["inventoryQuantity"] >= item.quantity
        )
        line_total = entry["priceCents"] * item.quantity
        if has_stock:
            subtotal += line_total
        else:
            any_unavailable = True

        items.append(
            {
                "id": item.id,
                "variant_id": item.variant_id,
                "sku": entry["sku"],
                "product_title": entry["productTitle"],
                "product_slug": entry["productSlug"],
                "variant_name": entry["name"],
                "image_url": entry["imageUrl"],
                "unit_price_cents": entry["priceCents"],
                "quantity": item.quantity,
                "total_cents": line_total,
                "available": has_stock,
            }
        )

    tax, shipping, total = compute_totals(subtotal, settings)
    return {
        "id": cart.id,
        "items": items,
        "subtotal_cents": subtotal,
        "tax_cents": tax,
        "shipping_cents": shipping,
        "total_cents": total,
        "currency": cart.currency,
        "item_count": sum(i.quantity for i in cart.items),
        "has_unavailable_items": any_unavailable,
    }


# -----------------------------------------------------------------------------
# Checkout
# -----------------------------------------------------------------------------


async def checkout(
    session: AsyncSession,
    *,
    cart: Cart,
    email: str,
    shipping_address: dict,
    billing_address: dict,
    user_id: UUID | None,
    settings: OrderSettings,
    catalog: CatalogClient,
    payments: PaymentsClient,
) -> tuple[Order, dict]:
    """Convert a cart into an order and start payment.

    The sequence, and why it is in this order:

    1. **Reprice from the catalogue.** Nothing monetary comes from the client.
    2. **Reserve inventory.** Before taking money, not after. Reserving second
       means a customer can be charged for something you cannot ship.
    3. **Create the order** in ``pending_payment``.
    4. **Create the Stripe payment intent** for the server-computed total.
    5. **Return the client secret**, which the browser uses to confirm the
       payment directly with Stripe.

    Steps 2-4 are not a distributed transaction, and cannot be: Stripe has no
    idea about our database transaction. So step 4 is compensated instead — if
    intent creation fails, the reserved stock is released and the order is
    cancelled. This is the saga pattern, and the compensating action is written
    to be unable to fail loudly (see `CatalogClient.release_inventory`).

    Note:
        The order reaches ``paid`` only via a signature-verified Stripe webhook
        (see `mark_order_paid`). A browser reporting "payment succeeded" is not
        evidence of anything — it is trivially forged — so the confirmation
        page reflects the order's real status rather than setting it.

    Args:
        session: Active session.
        cart: The cart being checked out.
        email: Where to send the receipt.
        shipping_address: Validated address dict.
        billing_address: Validated address dict.
        user_id: The buyer, or ``None`` for a guest.
        settings: Service settings.
        catalog: Catalogue client.
        payments: Payments client.

    Returns:
        ``(order, payment intent dict)``.

    Raises:
        ConflictError: If the cart is empty, already converted, or a line is
            out of stock.
        UpstreamError: If the catalogue or payments service is unreachable.
    """
    if cart.status != "open":
        raise ConflictError("This cart has already been checked out.")
    if not cart.items:
        raise ConflictError("Your cart is empty.")

    # --- 1. Authoritative pricing -------------------------------------------
    priced = await price_cart(cart, catalog, settings)
    if priced["has_unavailable_items"]:
        raise ConflictError(
            "Some items in your cart are no longer available. Review your cart and try again."
        )
    if priced["total_cents"] <= 0:
        raise ConflictError("Your cart total is zero.")

    lines = [(item.variant_id, item.quantity) for item in cart.items]

    # --- 2. Reserve stock ---------------------------------------------------
    # Raises ConflictError naming the SKU if anything is short.
    await catalog.reserve_inventory(lines)

    # --- 3. Create the order ------------------------------------------------
    try:
        order = Order(
            order_number=generate_order_number(),
            user_id=user_id,
            email=email.strip().lower(),
            status="pending_payment",
            subtotal_cents=priced["subtotal_cents"],
            tax_cents=priced["tax_cents"],
            shipping_cents=priced["shipping_cents"],
            total_cents=priced["total_cents"],
            currency=priced["currency"],
            shipping_address=shipping_address,
            billing_address=billing_address,
            cart_id=cart.id,
            placed_at=datetime.now(UTC),
        )
        for line in priced["items"]:
            order.items.append(
                OrderItem(
                    variant_id=line["variant_id"],
                    sku=line["sku"],
                    product_title=line["product_title"],
                    product_slug=line["product_slug"],
                    variant_name=line["variant_name"],
                    image_url=line["image_url"],
                    unit_price_cents=line["unit_price_cents"],
                    quantity=line["quantity"],
                    total_cents=line["total_cents"],
                )
            )
        order.events.append(
            OrderEvent(status="pending_payment", note="Order created; awaiting payment.")
        )

        # The cart is closed here, inside the same transaction as the order.
        # Two simultaneous checkout requests therefore cannot both succeed —
        # the second finds a converted cart and is rejected at the top of this
        # function.
        cart.status = "converted"

        session.add(order)
        await session.flush()
    except Exception:
        # The order never existed, so only the stock reservation needs undoing.
        await catalog.release_inventory(lines)
        raise

    # --- 4. Start the payment ----------------------------------------------
    try:
        intent = await payments.create_payment_intent(
            order_id=order.id,
            order_number=order.order_number,
            amount_cents=order.total_cents,
            currency=order.currency,
            email=order.email,
        )
    except Exception:
        # Compensate: release the stock and mark the order cancelled. Committed
        # explicitly, because the exception we re-raise would otherwise roll
        # back the cancellation and leave a pending order nobody will ever pay.
        await catalog.release_inventory(lines)
        order.status = "cancelled"
        order.cancelled_at = datetime.now(UTC)
        order.events.append(
            OrderEvent(status="cancelled", note="Could not start payment; stock released.")
        )
        await session.commit()
        log.error("checkout_payment_intent_failed", order_id=str(order.id))
        raise

    order.payment_intent_id = intent["paymentIntentId"]
    await session.flush()

    log.info(
        "checkout_completed",
        order_id=str(order.id),
        order_number=order.order_number,
        total_cents=order.total_cents,
    )
    return order, intent


# -----------------------------------------------------------------------------
# Order lifecycle
# -----------------------------------------------------------------------------


async def transition_order(
    session: AsyncSession,
    order: Order,
    *,
    new_status: str,
    note: str | None = None,
    actor_user_id: UUID | None = None,
) -> Order:
    """Move an order to a new status, enforcing the state machine.

    Every status change in the system goes through here. Centralising it means
    the legal transitions are defined once, in `ALLOWED_TRANSITIONS`, and no
    route can bypass them — an admin cannot mark a refunded order as fulfilled,
    and a retried webhook cannot move a delivered order back to paid.

    Args:
        session: Active session.
        order: The order to change.
        new_status: Target status.
        note: Free text recorded on the history entry.
        actor_user_id: Who did it; ``None`` for a system action.

    Returns:
        The updated order.

    Raises:
        ConflictError: If the transition is not legal from the current status.
    """
    if new_status == order.status:
        return order  # idempotent: re-applying the current status is a no-op

    allowed = ALLOWED_TRANSITIONS.get(order.status, ())
    if new_status not in allowed:
        raise ConflictError(
            f"An order that is {order.status.replace('_', ' ')} cannot become "
            f"{new_status.replace('_', ' ')}.",
            details={"currentStatus": order.status, "allowed": list(allowed)},
        )

    previous = order.status
    order.status = new_status

    now = datetime.now(UTC)
    if new_status == "paid":
        order.paid_at = now
    elif new_status == "cancelled":
        order.cancelled_at = now

    order.events.append(OrderEvent(status=new_status, note=note, actor_user_id=actor_user_id))
    await session.flush()

    log.info(
        "order_status_changed",
        order_id=str(order.id),
        order_number=order.order_number,
        **{"from": previous, "to": new_status},
    )
    return order


async def get_order(
    session: AsyncSession,
    order_id: UUID,
    *,
    requester_user_id: UUID | None = None,
    is_admin: bool = False,
) -> Order:
    """Fetch an order, enforcing ownership.

    Args:
        session: Active session.
        order_id: The order to fetch.
        requester_user_id: Who is asking.
        is_admin: Whether to skip the ownership check.

    Returns:
        The order with items and events loaded.

    Raises:
        NotFoundError: If it does not exist, **or** if the caller does not own
            it. Returning 404 rather than 403 for someone else's order means an
            attacker enumerating IDs cannot tell which ones are real.
        ForbiddenError: If a signed-out caller asks for a user's order.
    """
    result = await session.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.items), selectinload(Order.events))
    )
    order = result.scalar_one_or_none()
    if order is None:
        raise NotFoundError("Order not found.")

    if is_admin:
        return order

    if order.user_id is None:
        # A guest order has no owner to compare against. It is reachable only
        # by its unguessable ID, which is why order numbers are random.
        if requester_user_id is None:
            return order
        raise ForbiddenError("This order belongs to a guest checkout.")

    if order.user_id != requester_user_id:
        raise NotFoundError("Order not found.")

    return order


async def list_orders(
    session: AsyncSession,
    *,
    user_id: UUID | None = None,
    status: str | None = None,
    search: str | None = None,
    offset: int = 0,
    limit: int = 25,
) -> tuple[list[Order], int]:
    """List orders, optionally filtered.

    Args:
        session: Active session.
        user_id: Restrict to one customer. Omitted for the admin view.
        status: Filter by status.
        search: Match on order number or email.
        offset: SQL offset.
        limit: SQL limit.

    Returns:
        ``(orders, total matching count)``.
    """
    conditions = []
    if user_id is not None:
        conditions.append(Order.user_id == user_id)
    if status:
        conditions.append(Order.status == status)
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(Order.order_number.ilike(pattern) | Order.email.ilike(pattern))

    total = await session.scalar(select(func.count()).select_from(Order).where(*conditions))
    result = await session.execute(
        select(Order)
        .where(*conditions)
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all()), int(total or 0)


async def mark_order_paid(
    session: AsyncSession,
    *,
    payment_intent_id: str,
    amount_received_cents: int,
    notifications: NotificationsClient,
) -> Order:
    """Record a confirmed payment. Called only by the payments service.

    Two protections, both essential:

    * **Idempotency.** Stripe delivers webhooks at least once and retries on
      any non-2xx, so this *will* be called twice for the same payment. An
      order already in ``paid`` returns unchanged rather than re-running the
      transition or sending a second receipt.
    * **Amount verification.** The amount Stripe actually received is compared
      with the order total. A mismatch is logged and rejected rather than
      fulfilled — it means either a bug in our own intent creation or an
      attempt to pay a different amount than the order calls for.

    Args:
        session: Active session.
        payment_intent_id: Stripe's identifier, used to find the order.
        amount_received_cents: What Stripe says was actually captured.
        notifications: Client for the confirmation email.

    Returns:
        The paid order.

    Raises:
        NotFoundError: If no order matches the intent.
        ConflictError: If the captured amount does not match the order total.
    """
    result = await session.execute(
        select(Order)
        .where(Order.payment_intent_id == payment_intent_id)
        .options(selectinload(Order.items), selectinload(Order.events))
    )
    order = result.scalar_one_or_none()
    if order is None:
        raise NotFoundError("No order matches that payment.")

    if order.status == "paid":
        log.info("payment_webhook_duplicate_ignored", order_id=str(order.id))
        return order

    if amount_received_cents != order.total_cents:
        log.error(
            "payment_amount_mismatch",
            order_id=str(order.id),
            expected_cents=order.total_cents,
            received_cents=amount_received_cents,
        )
        raise ConflictError(
            "The payment amount does not match the order total.",
            details={
                "expectedCents": order.total_cents,
                "receivedCents": amount_received_cents,
            },
        )

    await transition_order(session, order, new_status="paid", note="Payment confirmed by Stripe.")

    await notifications.send(
        template="order_confirmation",
        to=order.email,
        context={
            "orderNumber": order.order_number,
            "totalCents": order.total_cents,
            "currency": order.currency,
            "items": [
                {
                    "title": item.product_title,
                    "variant": item.variant_name,
                    "quantity": item.quantity,
                    "totalCents": item.total_cents,
                }
                for item in order.items
            ],
        },
    )
    return order


async def mark_payment_failed(
    session: AsyncSession,
    *,
    payment_intent_id: str,
    reason: str | None,
    catalog: CatalogClient,
) -> Order | None:
    """Cancel an order whose payment failed, and return its stock.

    Args:
        session: Active session.
        payment_intent_id: Stripe's identifier.
        reason: Stripe's failure message, recorded on the history entry.
        catalog: Client used to release the reserved stock.

    Returns:
        The cancelled order, or ``None`` if no order matched or it was already
        in a terminal state. Returning ``None`` rather than raising keeps the
        webhook handler able to answer 200, which stops Stripe retrying
        something that will never succeed.
    """
    result = await session.execute(
        select(Order)
        .where(Order.payment_intent_id == payment_intent_id)
        .options(selectinload(Order.items), selectinload(Order.events))
    )
    order = result.scalar_one_or_none()
    if order is None or order.status != "pending_payment":
        return None

    await catalog.release_inventory([(item.variant_id, item.quantity) for item in order.items])
    await transition_order(
        session,
        order,
        new_status="cancelled",
        note=f"Payment failed: {reason}" if reason else "Payment failed.",
    )
    return order


async def save_order(session: AsyncSession, order: Order) -> None:
    """Flush an order, translating a duplicate order number into a retry.

    Args:
        session: Active session.
        order: The order to persist.

    Raises:
        ConflictError: On a collision, which is vanishingly unlikely but is
            handled rather than surfacing as a 500.
    """
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Could not place the order. Please try again.") from exc
