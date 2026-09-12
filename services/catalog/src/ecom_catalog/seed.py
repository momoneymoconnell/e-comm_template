"""Load placeholder catalogue data for local development.

    docker compose exec catalog python -m ecom_catalog.seed     (or: make seed)

The business this template will eventually serve is undecided, so these
products are deliberately generic placeholders — enough for the storefront and
admin console to have something real to render, lay out and paginate. Delete
this module, or rewrite the `DEMO_*` lists, once you know what you are selling.

Idempotent: it checks for each slug first, so running it twice does not create
duplicates and does not reset stock you have been testing against.
"""

from __future__ import annotations

import asyncio

from ecom_shared.db import Database
from ecom_shared.logging import configure_logging, get_logger
from sqlalchemy import select

from ecom_catalog.config import CatalogSettings
from ecom_catalog.models import Category, Product, ProductVariant

log = get_logger(__name__)

DEMO_CATEGORIES = [
    {"slug": "collection-one", "name": "Collection One", "position": 1},
    {"slug": "collection-two", "name": "Collection Two", "position": 2},
    {"slug": "archive", "name": "Archive", "position": 3},
]

DEMO_PRODUCTS = [
    {
        "slug": "placeholder-item-01",
        "title": "Placeholder Item 01",
        "subtitle": "Replace with your first product",
        "description": (
            "A stand-in record so the storefront has something to lay out. "
            "Swap the title, copy and imagery once the catalogue is decided."
        ),
        "category": "collection-one",
        "status": "active",
        "variants": [
            {"sku": "PH-01-S", "name": "Small", "price_cents": 4200, "qty": 25},
            {"sku": "PH-01-M", "name": "Medium", "price_cents": 5200, "qty": 18},
            {"sku": "PH-01-L", "name": "Large", "price_cents": 6400, "qty": 0},
        ],
    },
    {
        "slug": "placeholder-item-02",
        "title": "Placeholder Item 02",
        "subtitle": "A single-variant product",
        "description": "Demonstrates a product with exactly one variant.",
        "category": "collection-one",
        "status": "active",
        "variants": [
            {
                "sku": "PH-02",
                "name": "Standard",
                "price_cents": 12800,
                "compare_at": 15900,
                "qty": 7,
            }
        ],
    },
    {
        "slug": "placeholder-item-03",
        "title": "Placeholder Item 03",
        "subtitle": "Untracked stock",
        "description": "A digital or made-to-order item that never sells out.",
        "category": "collection-two",
        "status": "active",
        "variants": [
            {
                "sku": "PH-03",
                "name": "Digital",
                "price_cents": 2400,
                "qty": 0,
                "track": False,
            }
        ],
    },
    {
        "slug": "placeholder-item-04",
        "title": "Placeholder Item 04",
        "subtitle": "Not yet published",
        "description": "A draft, to confirm that drafts stay off the storefront.",
        "category": "collection-two",
        "status": "draft",
        "variants": [{"sku": "PH-04", "name": "Standard", "price_cents": 8800, "qty": 3}],
    },
]


async def seed() -> None:
    """Insert the demo categories and products if they are not already there."""
    settings = CatalogSettings()
    configure_logging(settings.service_name, settings.log_level, json_output=False)
    db = Database(settings.database_url, schema=settings.db_schema)

    async with db.session_factory() as session:
        categories: dict[str, Category] = {}
        for spec in DEMO_CATEGORIES:
            existing = (
                await session.execute(select(Category).where(Category.slug == spec["slug"]))
            ).scalar_one_or_none()
            if existing is None:
                existing = Category(slug=spec["slug"], name=spec["name"], position=spec["position"])
                session.add(existing)
                await session.flush()
                log.info("category_created", slug=spec["slug"])
            categories[spec["slug"]] = existing

        created = 0
        for spec in DEMO_PRODUCTS:
            exists = (
                await session.execute(select(Product).where(Product.slug == spec["slug"]))
            ).scalar_one_or_none()
            if exists is not None:
                continue

            product = Product(
                slug=spec["slug"],
                title=spec["title"],
                subtitle=spec["subtitle"],
                description=spec["description"],
                status=spec["status"],
                category_id=categories[spec["category"]].id,
            )
            for index, variant in enumerate(spec["variants"]):
                product.variants.append(
                    ProductVariant(
                        sku=variant["sku"],
                        name=variant["name"],
                        price_cents=variant["price_cents"],
                        compare_at_price_cents=variant.get("compare_at"),
                        inventory_quantity=variant["qty"],
                        track_inventory=variant.get("track", True),
                        position=index,
                    )
                )
            session.add(product)
            created += 1

        await session.commit()
        log.info("seed_complete", products_created=created)

    await db.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
