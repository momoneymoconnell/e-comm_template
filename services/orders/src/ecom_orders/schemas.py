"""Request and response models for carts and orders."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from ecom_shared.schemas import ApiModel
from pydantic import EmailStr, Field, field_validator, model_validator


class Address(ApiModel):
    """A postal address.

    Kept deliberately loose. Address formats vary enormously between countries
    — there is no universal "state", postcodes are alphanumeric in half the
    world, and some addresses have no street number at all. Over-validating
    here rejects real customers, which costs far more than it prevents.
    """

    full_name: str = Field(min_length=1, max_length=200)
    line1: str = Field(min_length=1, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=120)
    region: str | None = Field(default=None, max_length=120, description="State/province/county.")
    postal_code: str = Field(min_length=1, max_length=32)
    country: str = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2.")
    phone: str | None = Field(default=None, max_length=40)

    @field_validator("country")
    @classmethod
    def _uppercase_country(cls, value: str) -> str:
        """Normalise the country code; ISO 3166-1 alpha-2 is uppercase."""
        return value.upper()


# -----------------------------------------------------------------------------
# Cart
# -----------------------------------------------------------------------------


class AddToCartRequest(ApiModel):
    """Add a variant to the cart, or increase its quantity."""

    variant_id: UUID
    quantity: int = Field(default=1, ge=1, le=100)


class UpdateCartItemRequest(ApiModel):
    """Set a line's quantity. Zero removes the line."""

    quantity: int = Field(ge=0, le=100)


class CartItemResponse(ApiModel):
    """A cart line, priced live from the catalogue.

    Attributes:
        available: Whether the variant can still be bought at this quantity.
            The cart shows an unavailable line rather than silently dropping
            it, so the shopper understands why the total changed.
    """

    id: UUID
    variant_id: UUID
    sku: str
    product_title: str
    product_slug: str | None
    variant_name: str
    image_url: str | None
    unit_price_cents: int
    quantity: int
    total_cents: int
    available: bool


class CartResponse(ApiModel):
    """A cart with live pricing and computed totals."""

    id: UUID
    items: list[CartItemResponse]
    subtotal_cents: int
    discount_cents: int = 0
    discount_code: str | None = None
    #: Why a submitted code was not applied. Shown next to the input, so a
    #: shopper learns the code expired rather than silently seeing no change.
    discount_error: str | None = None
    tax_cents: int
    shipping_cents: int
    total_cents: int
    currency: str
    item_count: int
    has_unavailable_items: bool


# -----------------------------------------------------------------------------
# Checkout
# -----------------------------------------------------------------------------


class CheckoutRequest(ApiModel):
    """Turn a cart into an order and start payment.

    Note what is **not** here: prices, totals, or any monetary amount at all.
    Every figure is computed server-side from the catalogue. A checkout request
    that accepted a total would let anyone buy anything for a penny, and that
    is the single most commonly exploited e-commerce flaw.
    """

    email: EmailStr
    shipping_address: Address
    billing_address: Address | None = Field(
        default=None, description="Defaults to the shipping address when omitted."
    )
    discount_code: str | None = Field(default=None, max_length=40)


class CheckoutResponse(ApiModel):
    """The result of starting a checkout.

    Attributes:
        client_secret: Stripe's per-intent secret. The browser uses it to
            confirm the payment directly with Stripe, so card details never
            traverse our infrastructure. It is scoped to this one payment and
            is useless for anything else.
    """

    order_id: UUID
    order_number: str
    total_cents: int
    currency: str
    client_secret: str
    payment_intent_id: str


# -----------------------------------------------------------------------------
# Orders
# -----------------------------------------------------------------------------


class OrderItemResponse(ApiModel):
    """A purchased line, as recorded at the time of purchase."""

    id: UUID
    variant_id: UUID
    sku: str
    product_title: str
    product_slug: str | None
    variant_name: str
    image_url: str | None
    unit_price_cents: int
    quantity: int
    total_cents: int


class OrderEventResponse(ApiModel):
    """One entry in an order's history."""

    status: str
    note: str | None
    created_at: datetime


class OrderResponse(ApiModel):
    """A full order."""

    id: UUID
    order_number: str
    status: str
    email: EmailStr
    subtotal_cents: int
    discount_cents: int = 0
    discount_code: str | None = None
    tax_cents: int
    shipping_cents: int
    total_cents: int
    currency: str
    carrier: str | None = None
    tracking_number: str | None = None
    tracking_url: str | None = None
    shipped_at: datetime | None = None
    shipping_address: dict[str, Any]
    billing_address: dict[str, Any]
    items: list[OrderItemResponse]
    events: list[OrderEventResponse] = Field(default_factory=list)
    placed_at: datetime | None
    paid_at: datetime | None
    created_at: datetime


class OrderSummary(ApiModel):
    """A compact order, for list views."""

    id: UUID
    order_number: str
    status: str
    email: EmailStr
    total_cents: int
    currency: str
    item_count: int
    created_at: datetime


class UpdateOrderStatusRequest(ApiModel):
    """Admin: move an order to a new status.

    Marking an order fulfilled is the point at which shipping details are
    captured, because that is when they exist. Supplying them sends the
    customer a shipped email with a working tracking link.
    """

    status: str = Field(
        pattern="^(paid|fulfilled|delivered|cancelled|refunded)$",
        description="Target status. The transition must be legal from the current one.",
    )
    note: str | None = Field(default=None, max_length=1000)
    carrier: str | None = Field(default=None, max_length=60)
    tracking_number: str | None = Field(default=None, max_length=120)


class DiscountCodeResponse(ApiModel):
    """A promotional code, for the admin console."""

    id: UUID
    code: str
    description: str | None
    kind: str
    value: int
    min_subtotal_cents: int
    max_uses: int | None
    used_count: int
    starts_at: datetime | None
    ends_at: datetime | None
    is_active: bool
    created_at: datetime


class DiscountCodeWrite(ApiModel):
    """Create a promotional code."""

    code: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    description: str | None = Field(default=None, max_length=200)
    kind: str = Field(pattern="^(percent|fixed)$")
    value: int = Field(gt=0, description="Basis points for percent, minor units for fixed.")
    min_subtotal_cents: int = Field(default=0, ge=0)
    max_uses: int | None = Field(default=None, gt=0)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _check_value_and_dates(self) -> DiscountCodeWrite:
        """Reject values the database would refuse, with a message that explains.

        Both rules are also CHECK constraints, which is where the real
        guarantee lives. Validating here as well turns what would otherwise be
        an IntegrityError - reported by the route handler as "that code already
        exists", because that is the only integrity error it expects - into a
        422 that says what is actually wrong.

        Raises:
            ValueError: For a percentage above 100%, or an end date that falls
                before the start date.
        """
        if self.kind == "percent" and self.value > 10_000:
            raise ValueError("A percentage discount cannot exceed 100% (10000 basis points).")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("The end date must be after the start date.")
        return self


# -----------------------------------------------------------------------------
# Internal
# -----------------------------------------------------------------------------


class MarkPaidRequest(ApiModel):
    """Internal: payments telling orders that a payment succeeded.

    Only ever called by the payments service, after it has verified the Stripe
    webhook signature. Nothing reachable from a browser can mark an order paid.
    """

    payment_intent_id: str = Field(min_length=1, max_length=120)
    amount_received_cents: int = Field(ge=0)


class PaymentFailedRequest(ApiModel):
    """Internal: payments reporting a failed or cancelled payment."""

    payment_intent_id: str = Field(min_length=1, max_length=120)
    reason: str | None = Field(default=None, max_length=500)


class PurchaseCheckRequest(ApiModel):
    """Internal: has this customer actually bought any of these variants?

    Asked by the catalog service before accepting a product review. Reviews
    restricted to verified purchases are the single most effective spam filter
    a small shop has - it costs an attacker a real order to leave one.
    """

    user_id: UUID
    variant_ids: list[UUID] = Field(min_length=1, max_length=100)


class PurchaseCheckResponse(ApiModel):
    """Whether a paid order exists containing one of the variants."""

    purchased: bool
    first_purchased_at: datetime | None = None
