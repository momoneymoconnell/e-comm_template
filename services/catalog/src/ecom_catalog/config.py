"""Configuration for the catalog service."""

from __future__ import annotations

from ecom_shared.config import ServiceSettings


class CatalogSettings(ServiceSettings):
    """Settings for products, variants, categories, inventory and media.

    Attributes:
        media_root: Directory uploaded images are written to. A Docker volume,
            so it survives container restarts and rebuilds.
        orders_url: Used to check whether a reviewer actually bought the item.
    """

    service_name: str = "catalog"
    db_schema: str = "catalog"

    media_root: str = "/data/media"

    orders_url: str = "http://orders:8000"
