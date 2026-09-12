"""Payments service entrypoint."""

from __future__ import annotations

from ecom_shared.app import create_service_app
from ecom_shared.logging import get_logger
from fastapi import FastAPI

from ecom_payments.config import PaymentSettings
from ecom_payments.router import admin_router, internal_router, webhook_router
from ecom_payments.stripe_gateway import StripeGateway

log = get_logger(__name__)

settings = PaymentSettings()


async def _init_stripe(app: FastAPI) -> None:
    """Build the Stripe gateway once and warn loudly if it is unconfigured.

    Constructing the client per request would rebuild its connection pool and
    discard TLS sessions each time — pure added latency on the checkout path.

    A missing key is a warning rather than a hard failure so the rest of the
    stack still starts and can be developed against. Checkout will fail with a
    clear error until the key is set, and this log line at boot is where you
    find out why.
    """
    app.state.stripe = StripeGateway(settings)
    if not settings.stripe_configured:
        log.warning(
            "stripe_not_configured",
            hint="Add STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET to .env. "
            "Checkout will fail until you do.",
        )


app = create_service_app(
    settings,
    routers=[webhook_router, internal_router, admin_router],
    title="Payments Service",
    description=(
        "Stripe payment intents, webhooks and refunds.\n\n"
        "No card data is stored, processed or transmitted by this service. "
        "Card details go from the browser straight to Stripe via Stripe "
        "Elements; we only ever hold an opaque payment-intent ID, which is "
        "what keeps the system out of PCI-DSS scope.\n\n"
        "The webhook endpoint is public by necessity and its safety rests on "
        "HMAC signature verification: an unsigned or mis-signed event is "
        "rejected before any business logic runs."
    ),
    on_startup=[_init_stripe],
)
