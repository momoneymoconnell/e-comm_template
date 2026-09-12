"""Admin routes for managing the catalogue.

Guarded at the router level by `require_admin`, so a new endpoint added here is
protected by default.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from ecom_shared.errors import NotFoundError
from ecom_shared.identity import AdminUser, require_admin
from ecom_shared.schemas import Message, Page, PageParams
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ecom_catalog import service
from ecom_catalog.deps import get_db
from ecom_catalog.models import Category, Product, ProductVariant
from ecom_catalog.schemas import (
    AdminVariantResponse,
    CategoryResponse,
    CategoryWrite,
    ProductPatch,
    ProductResponse,
    ProductWrite,
    VariantWrite,
)

router = APIRouter(
    prefix="/catalog/admin", tags=["catalog-admin"], dependencies=[Depends(require_admin)]
)

Db = Annotated[AsyncSession, Depends(get_db)]
PageQuery = Annotated[PageParams, Depends()]


@router.get("/products", response_model=Page[ProductResponse], summary="List all products")
async def list_products(
    admin: AdminUser,
    db: Db,
    params: PageQuery,
    status: Annotated[str | None, Query(pattern="^(draft|active|archived)$")] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[ProductResponse]:
    """List products in every status, unlike the public endpoint."""
    products, total = await service.list_products(db, params, search=search, status=status)
    items = [ProductResponse.model_validate(p) for p in products]
    return Page[ProductResponse].build(items, total, params)


@router.post(
    "/products", response_model=ProductResponse, status_code=201, summary="Create a product"
)
async def create_product(payload: ProductWrite, admin: AdminUser, db: Db) -> ProductResponse:
    """Create a product together with its initial variants."""
    product = await service.create_product(db, payload)
    return ProductResponse.model_validate(product)


@router.patch("/products/{product_id}", response_model=ProductResponse, summary="Update a product")
async def update_product(
    product_id: UUID, payload: ProductPatch, admin: AdminUser, db: Db
) -> ProductResponse:
    """Apply a partial update. Omitted fields are left untouched."""
    product = await service.update_product(db, product_id, payload)
    return ProductResponse.model_validate(product)


@router.put(
    "/products/{product_id}/variants",
    response_model=ProductResponse,
    summary="Replace a product's variants",
)
async def replace_variants(
    product_id: UUID, payload: list[VariantWrite], admin: AdminUser, db: Db
) -> ProductResponse:
    """Replace the variant list, matching existing rows by SKU.

    Variants dropped from the payload are deactivated rather than deleted, so
    historical order lines keep resolving.
    """
    product = await service.replace_variants(db, product_id, payload)
    return ProductResponse.model_validate(product)


@router.get(
    "/products/{product_id}/variants",
    response_model=list[AdminVariantResponse],
    summary="Variants with real stock levels",
)
async def list_variants(product_id: UUID, admin: AdminUser, db: Db) -> list[AdminVariantResponse]:
    """Return a product's variants including exact inventory counts."""
    result = await db.execute(
        select(ProductVariant)
        .where(ProductVariant.product_id == product_id)
        .order_by(ProductVariant.position)
    )
    variants = list(result.scalars().all())
    return [
        AdminVariantResponse(
            id=v.id,
            sku=v.sku,
            name=v.name,
            price_cents=v.price_cents,
            compare_at_price_cents=v.compare_at_price_cents,
            currency=v.currency,
            in_stock=v.in_stock,
            position=v.position,
            inventory_quantity=v.inventory_quantity,
            track_inventory=v.track_inventory,
            is_active=v.is_active,
            weight_grams=v.weight_grams,
        )
        for v in variants
    ]


@router.delete("/products/{product_id}", response_model=Message, summary="Archive a product")
async def archive_product(product_id: UUID, admin: AdminUser, db: Db) -> Message:
    """Archive a product. Deliberately not a hard delete.

    Past orders reference this product's variants. Deleting the row would break
    every historical order that contains it — and tax law generally requires
    you to be able to reproduce an invoice years later.
    """
    product = await db.get(Product, product_id)
    if product is None:
        raise NotFoundError("Product not found.")
    product.status = "archived"
    return Message(message="Product archived.")


@router.get("/categories", response_model=list[CategoryResponse], summary="List all categories")
async def list_categories(admin: AdminUser, db: Db) -> list[CategoryResponse]:
    """List every category, including inactive ones."""
    rows = await service.list_categories(db, only_active=False)
    return [
        CategoryResponse(
            id=c.id,
            slug=c.slug,
            name=c.name,
            description=c.description,
            position=c.position,
            is_active=c.is_active,
            product_count=count,
        )
        for c, count in rows
    ]


@router.post(
    "/categories", response_model=CategoryResponse, status_code=201, summary="Create a category"
)
async def create_category(payload: CategoryWrite, admin: AdminUser, db: Db) -> CategoryResponse:
    """Create a category."""
    category = await service.create_category(db, payload)
    return CategoryResponse(
        id=category.id,
        slug=category.slug,
        name=category.name,
        description=category.description,
        position=category.position,
        is_active=category.is_active,
        product_count=0,
    )


@router.get("/stats", summary="Catalogue statistics for the dashboard")
async def catalog_stats(admin: AdminUser, db: Db) -> dict[str, int]:
    """Return headline catalogue counts in a single grouped query."""
    products = (
        await db.execute(
            select(
                func.count().label("total"),
                func.count().filter(Product.status == "active").label("active"),
                func.count().filter(Product.status == "draft").label("draft"),
            ).select_from(Product)
        )
    ).one()
    stock = (
        await db.execute(
            select(
                func.coalesce(func.sum(ProductVariant.inventory_quantity), 0),
                func.count()
                .filter(
                    ProductVariant.track_inventory.is_(True),
                    ProductVariant.inventory_quantity == 0,
                )
                .label("out_of_stock"),
            ).select_from(ProductVariant)
        )
    ).one()
    categories = await db.scalar(select(func.count()).select_from(Category))

    return {
        "totalProducts": int(products.total),
        "activeProducts": int(products.active),
        "draftProducts": int(products.draft),
        "categories": int(categories or 0),
        "unitsInStock": int(stock[0]),
        "outOfStockVariants": int(stock[1]),
    }
