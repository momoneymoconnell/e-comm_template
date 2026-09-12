"""Catalog service entrypoint."""

from __future__ import annotations

from ecom_shared.app import create_service_app

from ecom_catalog.admin_router import router as admin_router
from ecom_catalog.config import CatalogSettings
from ecom_catalog.internal_router import router as internal_router
from ecom_catalog.router import router as public_router

settings = CatalogSettings()

app = create_service_app(
    settings,
    routers=[public_router, admin_router, internal_router],
    title="Catalog Service",
    description=(
        "Products, variants, categories and inventory.\n\n"
        "Prices live here and nowhere else. The checkout flow asks this "
        "service what a cart costs rather than trusting the browser, which is "
        "what prevents a tampered cart from setting its own prices."
    ),
)
