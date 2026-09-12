"""Configuration for the catalog service."""

from __future__ import annotations

from ecom_shared.config import ServiceSettings


class CatalogSettings(ServiceSettings):
    """Settings for products, variants, categories and inventory."""

    service_name: str = "catalog"
    db_schema: str = "catalog"
