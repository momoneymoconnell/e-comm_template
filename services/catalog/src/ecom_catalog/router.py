"""Public storefront routes for the catalogue.

Everything here is readable without signing in, and everything here is
read-only. That combination means these are the highest-traffic endpoints in
the system and the ones most exposed to scraping, so each one bounds its own
result size.
"""

from __future__ import annotations

from typing import Annotated

from ecom_shared.schemas import Page, PageParams
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ecom_catalog import service
from ecom_catalog.deps import get_db
from ecom_catalog.models import Product
from ecom_catalog.schemas import CategoryResponse, ProductResponse

router = APIRouter(prefix="/catalog", tags=["catalog"])


def to_public(product: Product) -> ProductResponse:
    """Build the storefront view of a product, hiding inactive variants.

    Replacing a product's variants deactivates the ones that were dropped
    rather than deleting them, because order lines still reference those rows.
    They must not appear in the shop, though - without this filter, editing a
    product leaves its removed options on sale, which is how a customer ends up
    buying something that no longer exists.

    Filtering happens on the ORM object rather than the response model, because
    `is_active` is deliberately absent from the public variant schema.

    Args:
        product: The loaded product, with variants and images.

    Returns:
        The response with only purchasable variants.
    """
    response = ProductResponse.model_validate(product)
    sellable = {variant.id for variant in product.variants if variant.is_active}
    response.variants = [variant for variant in response.variants if variant.id in sellable]
    return response


Db = Annotated[AsyncSession, Depends(get_db)]
PageQuery = Annotated[PageParams, Depends()]


@router.get("/products", response_model=Page[ProductResponse], summary="List products")
async def list_products(
    db: Db,
    params: PageQuery,
    category: Annotated[str | None, Query(max_length=120)] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[ProductResponse]:
    """Return a page of active products.

    Only ``active`` products are returned, and the status filter is applied
    here rather than being a parameter — a public endpoint that accepts
    ``?status=draft`` is a preview of everything you have not launched yet.

    Args:
        db: Active session.
        params: Pagination, capped at 100 items per page.
        category: Restrict to one category slug.
        search: Case-insensitive substring match on title and subtitle.

    Returns:
        A page of products with their variants.
    """
    products, total = await service.list_products(
        db, params, category_slug=category, search=search, status="active"
    )
    items = [to_public(p) for p in products]
    return Page[ProductResponse].build(items, total, params)


@router.get("/products/{slug}", response_model=ProductResponse, summary="Get one product")
async def get_product(slug: str, db: Db) -> ProductResponse:
    """Return a single active product by slug.

    A draft or archived product returns 404 rather than 403, so its existence
    is not confirmed to someone guessing URLs.
    """
    product = await service.get_product_by_slug(db, slug, only_active=True)
    return to_public(product)


@router.get("/categories", response_model=list[CategoryResponse], summary="List categories")
async def list_categories(db: Db) -> list[CategoryResponse]:
    """Return active categories with their active-product counts.

    Not paginated: a storefront has tens of categories, not thousands, and the
    navigation menu needs all of them at once.
    """
    rows = await service.list_categories(db, only_active=True)
    return [
        CategoryResponse(
            id=category.id,
            slug=category.slug,
            name=category.name,
            description=category.description,
            position=category.position,
            is_active=category.is_active,
            product_count=count,
        )
        for category, count in rows
    ]
