"""Orders service entrypoint."""

from __future__ import annotations

from ecom_shared.app import create_service_app

from ecom_orders.admin_router import router as admin_router
from ecom_orders.config import OrderSettings
from ecom_orders.internal_router import router as internal_router
from ecom_orders.router import router as public_router

settings = OrderSettings()

app = create_service_app(
    settings,
    # Order matters. FastAPI matches routes in registration order, and the
    # customer router ends with `GET /orders/{order_id}` - a catch-all for one
    # path segment. Registered first, it swallows `GET /orders/admin` and tries
    # to parse "admin" as a UUID, so the admin order list returns 422 and is
    # effectively unreachable.
    #
    # More specific routers first; the catch-all last.
    routers=[admin_router, internal_router, public_router],
    title="Orders Service",
    description=(
        "Carts, checkout and the order lifecycle.\n\n"
        "No monetary amount is ever accepted from the client: totals are "
        "recomputed from the catalogue at checkout. An order becomes paid only "
        "on a signature-verified Stripe webhook, never because the browser "
        "reported success."
    ),
)
