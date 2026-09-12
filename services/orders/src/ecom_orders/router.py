"""Customer-facing routes: the cart, checkout, and your own orders.

Carts work for guests as well as signed-in shoppers. A guest is identified by
an opaque token in an httpOnly cookie; signing in claims that cart rather than
discarding it, because losing a cart at the login step is one of the most
common places a checkout is abandoned.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from ecom_shared.identity import CurrentUser, MaybeUser
from ecom_shared.schemas import Page, PageParams
from ecom_shared.security import generate_token
from fastapi import APIRouter, Depends, Query, Request, Response, status

from ecom_orders import service
from ecom_orders.config import OrderSettings
from ecom_orders.deps import Catalog, Db, Payments, Settings
from ecom_orders.schemas import (
    AddToCartRequest,
    CartResponse,
    CheckoutRequest,
    CheckoutResponse,
    OrderResponse,
    OrderSummary,
    UpdateCartItemRequest,
)

router = APIRouter(prefix="/orders", tags=["orders"])

PageQuery = Annotated[PageParams, Depends()]

#: Cookie holding a guest's cart token.
#:
#: httpOnly, because possession of this token is what identifies the cart, so a
#: script must not be able to read it. Long-lived, because abandoning a cart on
#: Friday and returning on Monday is entirely normal shopping behaviour.
CART_COOKIE_NAME = "cart_token"
CART_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _read_cart_token(request: Request) -> str | None:
    """Read the guest cart token from the request cookies."""
    return request.cookies.get(CART_COOKIE_NAME)


def _issue_cart_token(response: Response, settings: OrderSettings, existing: str | None) -> str:
    """Return the guest cart token, minting and setting one if absent.

    Args:
        response: The outgoing response, mutated with the cookie.
        settings: Supplies the environment, which decides the Secure flag.
        existing: The token already present on the request, if any.

    Returns:
        The token to associate with this cart.
    """
    if existing:
        return existing

    token = generate_token(24)
    response.set_cookie(
        key=CART_COOKIE_NAME,
        value=token,
        max_age=CART_COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )
    return token


# -----------------------------------------------------------------------------
# Cart
# -----------------------------------------------------------------------------


@router.get("/cart", response_model=CartResponse, summary="Get the current cart")
async def get_cart(
    request: Request,
    response: Response,
    identity: MaybeUser,
    db: Db,
    settings: Settings,
    catalog: Catalog,
) -> CartResponse:
    """Return the caller's cart, priced against the live catalogue.

    Works signed in or as a guest. Prices are fetched fresh on every call
    rather than stored on the cart, so a cart opened last week reflects today's
    prices and today's availability.
    """
    token = _issue_cart_token(response, settings, _read_cart_token(request))
    cart = await service.get_or_create_cart(
        db, user_id=identity.user_id if identity else None, guest_token=token
    )
    priced = await service.price_cart(cart, catalog, settings)
    return CartResponse.model_validate(priced)


@router.post(
    "/cart/items",
    response_model=CartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an item to the cart",
)
async def add_item(
    payload: AddToCartRequest,
    request: Request,
    response: Response,
    identity: MaybeUser,
    db: Db,
    settings: Settings,
    catalog: Catalog,
) -> CartResponse:
    """Add a variant to the cart, or increase its quantity if already present."""
    token = _issue_cart_token(response, settings, _read_cart_token(request))
    cart = await service.get_or_create_cart(
        db, user_id=identity.user_id if identity else None, guest_token=token
    )
    cart = await service.add_to_cart(
        db, cart, variant_id=payload.variant_id, quantity=payload.quantity
    )
    priced = await service.price_cart(cart, catalog, settings)
    return CartResponse.model_validate(priced)


@router.patch(
    "/cart/items/{item_id}", response_model=CartResponse, summary="Change a line's quantity"
)
async def update_item(
    item_id: UUID,
    payload: UpdateCartItemRequest,
    request: Request,
    response: Response,
    identity: MaybeUser,
    db: Db,
    settings: Settings,
    catalog: Catalog,
) -> CartResponse:
    """Set a line's quantity. Zero removes the line.

    The line is looked up within the caller's own cart, so a line ID belonging
    to someone else simply does not match and reports 404.
    """
    token = _issue_cart_token(response, settings, _read_cart_token(request))
    cart = await service.get_or_create_cart(
        db, user_id=identity.user_id if identity else None, guest_token=token
    )
    cart = await service.set_cart_item_quantity(
        db, cart, item_id=item_id, quantity=payload.quantity
    )
    priced = await service.price_cart(cart, catalog, settings)
    return CartResponse.model_validate(priced)


@router.delete("/cart/items/{item_id}", response_model=CartResponse, summary="Remove a line")
async def remove_item(
    item_id: UUID,
    request: Request,
    response: Response,
    identity: MaybeUser,
    db: Db,
    settings: Settings,
    catalog: Catalog,
) -> CartResponse:
    """Remove a line from the cart."""
    token = _issue_cart_token(response, settings, _read_cart_token(request))
    cart = await service.get_or_create_cart(
        db, user_id=identity.user_id if identity else None, guest_token=token
    )
    cart = await service.set_cart_item_quantity(db, cart, item_id=item_id, quantity=0)
    priced = await service.price_cart(cart, catalog, settings)
    return CartResponse.model_validate(priced)


# -----------------------------------------------------------------------------
# Checkout
# -----------------------------------------------------------------------------


@router.post(
    "/checkout",
    response_model=CheckoutResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Turn the cart into an order and start payment",
)
async def checkout(
    payload: CheckoutRequest,
    request: Request,
    response: Response,
    identity: MaybeUser,
    db: Db,
    settings: Settings,
    catalog: Catalog,
    payments: Payments,
) -> CheckoutResponse:
    """Create an order from the cart and return a Stripe client secret.

    The request body carries no prices — see `CheckoutRequest`. Every amount is
    recomputed here from the catalogue.

    The returned `clientSecret` lets the browser confirm the payment directly
    with Stripe. The order becomes ``paid`` only when Stripe tells us so via a
    signature-verified webhook, never because the browser said it worked.
    """
    token = _issue_cart_token(response, settings, _read_cart_token(request))
    cart = await service.get_or_create_cart(
        db, user_id=identity.user_id if identity else None, guest_token=token
    )

    billing = payload.billing_address or payload.shipping_address
    order, intent = await service.checkout(
        db,
        cart=cart,
        email=payload.email,
        shipping_address=payload.shipping_address.model_dump(mode="json"),
        billing_address=billing.model_dump(mode="json"),
        user_id=identity.user_id if identity else None,
        settings=settings,
        catalog=catalog,
        payments=payments,
    )

    return CheckoutResponse(
        order_id=order.id,
        order_number=order.order_number,
        total_cents=order.total_cents,
        currency=order.currency,
        client_secret=intent["clientSecret"],
        payment_intent_id=intent["paymentIntentId"],
    )


# -----------------------------------------------------------------------------
# Orders
# -----------------------------------------------------------------------------


@router.get("", response_model=Page[OrderSummary], summary="Your order history")
async def list_my_orders(
    identity: CurrentUser,
    db: Db,
    params: PageQuery,
    order_status: Annotated[str | None, Query(alias="status", max_length=30)] = None,
) -> Page[OrderSummary]:
    """Return the signed-in customer's own orders.

    The `user_id` filter is the authorisation: there is no code path here that
    can return someone else's order, because the query never selects them.
    """
    orders, total = await service.list_orders(
        db,
        user_id=identity.user_id,
        status=order_status,
        offset=params.offset,
        limit=params.limit,
    )
    items = [
        OrderSummary(
            id=o.id,
            order_number=o.order_number,
            status=o.status,
            email=o.email,
            total_cents=o.total_cents,
            currency=o.currency,
            item_count=sum(i.quantity for i in o.items),
            created_at=o.created_at,
        )
        for o in orders
    ]
    return Page[OrderSummary].build(items, total, params)


@router.get("/{order_id}", response_model=OrderResponse, summary="Get one order")
async def get_order(order_id: UUID, identity: MaybeUser, db: Db) -> OrderResponse:
    """Return a single order, if it belongs to the caller.

    Someone else's order reports 404, not 403 — confirming that an ID exists is
    itself information worth withholding.
    """
    order = await service.get_order(
        db,
        order_id,
        requester_user_id=identity.user_id if identity else None,
        is_admin=False,
    )
    return OrderResponse.model_validate(order)
