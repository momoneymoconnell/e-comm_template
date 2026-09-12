"""Database tables for carts and orders (schema ``orders``).

The central design decision here is that **an order line is a snapshot, not a
reference**.

An `OrderItem` stores the SKU, the product title, the variant name and the unit
price as they were at the moment of purchase. It does not join to the catalogue
to display them. That is deliberate:

* Prices change. An invoice must show what the customer actually paid, not what
  the item costs today.
* Products get renamed, and archived, and occasionally deleted by mistake. An
  order from last year has to keep rendering regardless.
* You are generally required to be able to reproduce a historical invoice
  exactly, for years, for tax purposes.

`variant_id` is kept alongside the snapshot for analytics and reordering, but
nothing about displaying the order depends on it still resolving.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from ecom_shared.db import build_metadata, utcnow_sql
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for the orders service.

    Its metadata carries schema="orders", so every table declared
    against this base is created inside that schema.
    """

    metadata = build_metadata("orders")


#: Cart lifecycle.
CART_STATUSES = ("open", "converted", "abandoned")

#: Order lifecycle.
#:
#: ``pending_payment`` — created, stock reserved, awaiting Stripe confirmation.
#: ``paid``            — payment confirmed by a verified webhook.
#: ``fulfilled``       — dispatched.
#: ``delivered``       — received.
#: ``cancelled``       — abandoned or failed before payment; stock released.
#: ``refunded``        — money returned after payment.
ORDER_STATUSES = (
    "pending_payment",
    "paid",
    "fulfilled",
    "delivered",
    "cancelled",
    "refunded",
)

#: Which transitions are legal. Enforced in `service.transition_order`.
#:
#: Encoding this as data rather than as a chain of `if` statements means the
#: rules are auditable at a glance, and adding a status is one line rather than
#: a hunt through the code. Terminal states map to an empty tuple: a refunded
#: order cannot become fulfilled, and an admin mis-click should be rejected
#: rather than silently corrupt the books.
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "pending_payment": ("paid", "cancelled"),
    "paid": ("fulfilled", "refunded", "cancelled"),
    "fulfilled": ("delivered", "refunded"),
    "delivered": ("refunded",),
    "cancelled": (),
    "refunded": (),
}


class Cart(Base):
    """A shopping cart, belonging either to a signed-in user or to a guest.

    Attributes:
        user_id: The owner, once signed in. ``NULL`` for a guest cart.
        session_token_hash: SHA-256 of the opaque token in the guest's cookie.
            Hashed rather than stored plainly for the same reason session
            tokens are: a database dump should not hand over live carts, which
            may contain a shopper's intentions and, after checkout begins,
            their address.
        status: See `CART_STATUSES`. A cart becomes ``converted`` when its
            order is created, so it is never checked out twice.
    """

    __tablename__ = "carts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    session_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="usd")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql(), onupdate=func.now()
    )

    items: Mapped[list[CartItem]] = relationship(
        back_populates="cart", cascade="all, delete-orphan", order_by="CartItem.created_at"
    )

    __table_args__ = (
        CheckConstraint(f"status IN {CART_STATUSES}", name="status_valid"),
        Index("ix_carts_user_status", "user_id", "status"),
    )


class CartItem(Base):
    """One line in a cart.

    No price is stored. A cart holds *intent* — which variant, how many — and
    is repriced from the catalogue every time it is displayed or checked out.
    Storing a price here would mean a cart left open overnight could be checked
    out at yesterday's price, which is a discount anyone can trigger by waiting.
    """

    __tablename__ = "cart_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    cart_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("carts.id", ondelete="CASCADE"), nullable=False
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )

    cart: Mapped[Cart] = relationship(back_populates="items")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("quantity <= 100", name="quantity_reasonable"),
        # One row per variant per cart. Adding the same variant again bumps the
        # quantity rather than creating a second line, which keeps the cart
        # readable and the checkout arithmetic simple.
        UniqueConstraint("cart_id", "variant_id", name="uq_cart_items_cart_id_variant"),
    )


class Order(Base):
    """A placed order.

    Attributes:
        order_number: Human-readable reference, e.g. ``ORD-20260912-K3F9QX``.
            What a customer quotes in an email. Distinct from the UUID primary
            key, which is what systems use.
        email: Captured on the order itself, not looked up from the user. A
            guest order has no user, and a customer who later changes their
            email must not retroactively change where past receipts appear
            to have gone.
        subtotal/tax/shipping/total: All in minor units, all stored rather than
            recomputed. A recomputed total would change if the tax rate ever
            did, silently rewriting historical invoices.
        payment_intent_id: Stripe's identifier. Opaque to us — no card data
            ever touches this database, which is what keeps the system out of
            PCI-DSS scope.
        shipping_address: JSONB rather than columns. Address shape varies by
            country far more than a fixed schema can accommodate, and it is
            captured once and read back verbatim.
    """

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    order_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="pending_payment"
    )

    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    shipping_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="usd")

    shipping_address: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    billing_address: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    payment_intent_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    cart_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql(), onupdate=func.now()
    )

    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    events: Mapped[list[OrderEvent]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderEvent.created_at",
    )

    __table_args__ = (
        CheckConstraint(f"status IN {ORDER_STATUSES}", name="status_valid"),
        CheckConstraint("subtotal_cents >= 0", name="subtotal_non_negative"),
        CheckConstraint("total_cents >= 0", name="total_non_negative"),
        # The arithmetic must hold at the storage layer. A bug in the checkout
        # calculation should fail loudly on insert rather than quietly charge
        # the wrong amount.
        CheckConstraint(
            "total_cents = subtotal_cents + tax_cents + shipping_cents",
            name="total_is_sum_of_parts",
        ),
        Index("ix_orders_user_created", "user_id", "created_at"),
        Index("ix_orders_status_created", "status", "created_at"),
    )


class OrderItem(Base):
    """One purchased line, snapshotted at the moment of purchase.

    See the module docstring for why the product details are copied here rather
    than joined from the catalogue.
    """

    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )

    #: Kept for analytics and reordering. Nothing about rendering the order
    #: depends on this still resolving to a live catalogue row.
    variant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    product_title: Mapped[str] = mapped_column(String(200), nullable=False)
    product_slug: Mapped[str | None] = mapped_column(String(160))
    variant_name: Mapped[str] = mapped_column(String(160), nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(500))

    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )

    order: Mapped[Order] = relationship(back_populates="items")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price_cents >= 0", name="unit_price_non_negative"),
        CheckConstraint("total_cents = unit_price_cents * quantity", name="line_total_consistent"),
        Index("ix_order_items_order", "order_id"),
        Index("ix_order_items_variant", "variant_id"),
    )


class OrderEvent(Base):
    """An append-only entry in an order's history.

    Every status change writes one. This is what answers "when was this
    dispatched, and who marked it so" — questions that come up in every
    customer-service conversation and every dispute.
    """

    __tablename__ = "order_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    #: Who caused it: a user ID, or ``NULL`` for a system action such as a
    #: Stripe webhook.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )

    order: Mapped[Order] = relationship(back_populates="events")

    __table_args__ = (Index("ix_order_events_order_time", "order_id", "created_at"),)
