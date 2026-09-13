"""Catalog service entrypoint."""

from __future__ import annotations

from ecom_shared.app import create_service_app
from ecom_shared.logging import get_logger
from fastapi import FastAPI

from ecom_catalog.admin_router import router as admin_router
from ecom_catalog.config import CatalogSettings
from ecom_catalog.internal_router import router as internal_router
from ecom_catalog.media import LocalMediaStorage
from ecom_catalog.media_router import admin_router as media_admin_router
from ecom_catalog.media_router import gallery_router
from ecom_catalog.media_router import public_router as media_public_router
from ecom_catalog.router import router as public_router

log = get_logger(__name__)

settings = CatalogSettings()


async def _init_media(app: FastAPI) -> None:
    """Create the media storage backend and its directory.

    Built once at startup rather than per request: constructing it creates the
    directory, and doing that on every upload is pointless syscalls.
    """
    app.state.media = LocalMediaStorage(settings.media_root)
    log.info("media_storage_ready", root=settings.media_root)


app = create_service_app(
    settings,
    # Specific routes before the catch-all. `/catalog/media/{filename}` and
    # `/catalog/admin/...` must be matched before the public router's
    # `/catalog/products/{slug}` pattern gets a chance at them.
    routers=[
        media_public_router,
        media_admin_router,
        gallery_router,
        admin_router,
        internal_router,
        public_router,
    ],
    title="Catalog Service",
    description=(
        "Products, variants, categories, inventory and product imagery.\n\n"
        "Prices live here and nowhere else. The checkout flow asks this "
        "service what a cart costs rather than trusting the browser, which is "
        "what prevents a tampered cart from setting its own prices.\n\n"
        "Uploaded images are decoded, stripped of metadata, resized and "
        "re-encoded before being stored under a content-addressed name."
    ),
    on_startup=[_init_media],
)
