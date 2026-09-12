"""Configuration for the orders service."""

from __future__ import annotations

from ecom_shared.config import ServiceSettings


class OrderSettings(ServiceSettings):
    """Settings for carts, checkout and the order lifecycle.

    Attributes:
        catalog_url: Where to fetch authoritative prices and move inventory.
        payments_url: Where to create payment intents.
        notifications_url: Where to dispatch confirmation emails.
        tax_rate_basis_points: Flat tax rate in basis points (1/100th of a
            percent), so 825 means 8.25%. A placeholder: real sales tax depends
            on the buyer's jurisdiction and, once you are selling for real,
            belongs in a dedicated tax service or a provider like Stripe Tax.
            Basis points keep it an integer, which keeps the arithmetic exact.
        free_shipping_threshold_cents: Order subtotal above which shipping is
            free. Set to 0 to always charge.
        flat_shipping_cents: Flat shipping charge below that threshold.
        cart_ttl_days: How long an abandoned cart is kept before cleanup.
    """

    service_name: str = "orders"
    db_schema: str = "orders"

    catalog_url: str = "http://catalog:8000"
    payments_url: str = "http://payments:8000"
    notifications_url: str = "http://notifications:8000"

    tax_rate_basis_points: int = 0
    free_shipping_threshold_cents: int = 7500
    flat_shipping_cents: int = 695
    cart_ttl_days: int = 30
