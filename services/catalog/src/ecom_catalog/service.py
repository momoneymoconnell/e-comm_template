"""Catalogue business logic: queries, writes and inventory movements.

The interesting part of this module is `reserve_inventory`, which is where
concurrency actually bites. Everything else is ordinary CRUD.
"""

from __future__ import annotations

from uuid import UUID

from ecom_shared.errors import ConflictError, NotFoundError, ValidationFailedError
from ecom_shared.logging import get_logger
from ecom_shared.schemas import PageParams
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ecom_catalog.models import Category, Product, ProductVariant
from ecom_catalog.schemas import (
    CategoryWrite,
    ProductPatch,
    ProductWrite,
    VariantWrite,
)

log = get_logger(__name__)


# -----------------------------------------------------------------------------
# Reads
# -----------------------------------------------------------------------------


async def list_products(
    session: AsyncSession,
    params: PageParams,
    *,
    category_slug: str | None = None,
    search: str | None = None,
    status: str | None = "active",
) -> tuple[list[Product], int]:
    """Return a page of products with their variants and category.

    Uses `selectinload` for the relationships. The alternative — lazy loading —
    would issue one query per product for its variants and another for its
    category, so a 25-product page becomes 51 round trips. That is the N+1
    problem, and it is the usual reason a product listing is slow.

    Args:
        session: Active session.
        params: Pagination.
        category_slug: Restrict to one category.
        search: Case-insensitive substring match on title and subtitle.
        status: Filter by lifecycle status. Defaults to ``"active"``, so the
            public endpoint cannot accidentally expose drafts; pass ``None``
            from admin routes to see everything.

    Returns:
        ``(products, total matching count)``.
    """
    conditions = []
    if status is not None:
        conditions.append(Product.status == status)
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(Product.title.ilike(pattern) | Product.subtitle.ilike(pattern))
    if category_slug:
        conditions.append(
            Product.category_id.in_(select(Category.id).where(Category.slug == category_slug))
        )

    total = await session.scalar(select(func.count()).select_from(Product).where(*conditions))
    result = await session.execute(
        select(Product)
        .where(*conditions)
        .options(selectinload(Product.variants), selectinload(Product.category))
        .order_by(Product.position, Product.created_at.desc())
        .offset(params.offset)
        .limit(params.limit)
    )
    return list(result.scalars().all()), int(total or 0)


async def get_product_by_slug(
    session: AsyncSession, slug: str, *, only_active: bool = True
) -> Product:
    """Fetch one product by its slug.

    Args:
        session: Active session.
        slug: The product's URL slug.
        only_active: When ``True``, a draft or archived product reports 404.
            The public route uses this so an unreleased product cannot be
            previewed by guessing its URL.

    Returns:
        The product with variants and category loaded.

    Raises:
        NotFoundError: If no matching product is visible to this caller.
    """
    conditions = [Product.slug == slug]
    if only_active:
        conditions.append(Product.status == "active")

    result = await session.execute(
        select(Product)
        .where(*conditions)
        .options(selectinload(Product.variants), selectinload(Product.category))
    )
    product = result.scalar_one_or_none()
    if product is None:
        raise NotFoundError("Product not found.")
    return product


async def list_categories(
    session: AsyncSession, *, only_active: bool = True
) -> list[tuple[Category, int]]:
    """List categories with a count of their active products.

    Args:
        session: Active session.
        only_active: Hide inactive categories.

    Returns:
        ``(category, active product count)`` pairs, in display order.
    """
    conditions = [Category.is_active.is_(True)] if only_active else []

    # One grouped query with an outer join rather than a count per category.
    # The join must be filtered to active products, which is why the condition
    # sits in the ON clause and not in WHERE — in WHERE it would drop empty
    # categories entirely instead of showing them with a count of zero.
    result = await session.execute(
        select(Category, func.count(Product.id))
        .outerjoin(
            Product,
            (Product.category_id == Category.id) & (Product.status == "active"),
        )
        .where(*conditions)
        .group_by(Category.id)
        .order_by(Category.position, Category.name)
    )
    return [(row[0], int(row[1])) for row in result.all()]


async def get_variants(session: AsyncSession, variant_ids: list[UUID]) -> list[ProductVariant]:
    """Fetch variants by ID, with their products loaded.

    Args:
        session: Active session.
        variant_ids: The IDs to fetch.

    Returns:
        The matching variants. Missing IDs are simply absent; the caller
        decides whether that is an error.
    """
    result = await session.execute(
        select(ProductVariant)
        .where(ProductVariant.id.in_(variant_ids))
        .options(selectinload(ProductVariant.product))
    )
    return list(result.scalars().all())


# -----------------------------------------------------------------------------
# Inventory
# -----------------------------------------------------------------------------


async def reserve_inventory(session: AsyncSession, lines: list[tuple[UUID, int]]) -> None:
    """Atomically decrement stock for several variants.

    This is the one place in the catalogue where concurrency matters, so it is
    worth being precise about the approach.

    The naive version reads the quantity, checks it in Python, then writes back
    the new value. Two checkouts for the last unit can both read ``1``, both
    conclude there is enough, and both write ``0`` — one item sold twice. That
    is a read-modify-write race, and no amount of application-level checking
    fixes it.

    Instead, each line is a single conditional UPDATE::

        UPDATE product_variants
           SET inventory_quantity = inventory_quantity - :qty
         WHERE id = :id AND inventory_quantity >= :qty

    The database evaluates the condition and applies the decrement in one
    atomic statement, taking a row lock for its duration. Concurrent attempts
    serialise on that lock, so the second one sees the already-decremented
    value and matches zero rows — which we detect and reject. The
    ``inventory_quantity >= 0`` CHECK constraint is the backstop beneath that.

    Variants with ``track_inventory = false`` are skipped rather than
    decremented, so a service or digital product never runs out.

    Args:
        session: Active session. All lines share its transaction, so a failure
            on the third line rolls back the first two.
        lines: ``(variant_id, quantity)`` pairs.

    Raises:
        ConflictError: If any line cannot be satisfied. The message names the
            SKU so the storefront can point at the right cart row.
    """
    for variant_id, quantity in lines:
        result = await session.execute(
            update(ProductVariant)
            .where(
                ProductVariant.id == variant_id,
                ProductVariant.is_active.is_(True),
                # A variant that does not track inventory always matches.
                (ProductVariant.track_inventory.is_(False))
                | (ProductVariant.inventory_quantity >= quantity),
            )
            .values(
                # A CASE, not a plain subtraction: variants that do not track
                # inventory must match the WHERE clause (so the reservation
                # succeeds) without their quantity moving. Subtracting from
                # them unconditionally would drive a service or digital
                # product's count negative and trip the CHECK constraint.
                inventory_quantity=case(
                    (
                        ProductVariant.track_inventory.is_(False),
                        ProductVariant.inventory_quantity,
                    ),
                    else_=ProductVariant.inventory_quantity - quantity,
                )
            )
            .returning(ProductVariant.id)
        )
        if result.scalar_one_or_none() is None:
            # Either the variant does not exist, is inactive, or there is not
            # enough stock. Look it up to produce a message a shopper can act on.
            variant = await session.get(ProductVariant, variant_id)
            if variant is None:
                raise NotFoundError("One of the items in your cart no longer exists.")
            raise ConflictError(
                f"Only {variant.inventory_quantity} left of {variant.sku}.",
                details={
                    "variantId": str(variant_id),
                    "sku": variant.sku,
                    "available": variant.inventory_quantity,
                    "requested": quantity,
                },
            )
    log.info("inventory_reserved", lines=len(lines))


async def release_inventory(session: AsyncSession, lines: list[tuple[UUID, int]]) -> None:
    """Return reserved stock, after a cancelled or failed order.

    Deliberately forgiving: a variant that has since been deleted is skipped
    rather than raising. This runs on the compensating path of a failed
    checkout, and a release that throws would leave stock permanently
    unavailable — strictly worse than the missing row it is complaining about.

    Args:
        session: Active session.
        lines: ``(variant_id, quantity)`` pairs to add back.
    """
    for variant_id, quantity in lines:
        await session.execute(
            update(ProductVariant)
            .where(
                ProductVariant.id == variant_id,
                ProductVariant.track_inventory.is_(True),
            )
            .values(inventory_quantity=ProductVariant.inventory_quantity + quantity)
        )
    log.info("inventory_released", lines=len(lines))


# -----------------------------------------------------------------------------
# Writes (admin)
# -----------------------------------------------------------------------------


async def create_product(session: AsyncSession, payload: ProductWrite) -> Product:
    """Create a product and its variants.

    Args:
        session: Active session.
        payload: A validated `ProductWrite`.

    Returns:
        The created product.

    Raises:
        ConflictError: If the slug or any SKU is already taken.
    """
    product = Product(
        slug=payload.slug,
        title=payload.title,
        subtitle=payload.subtitle,
        description=payload.description,
        status=payload.status,
        category_id=payload.category_id,
        image_url=payload.image_url,
        position=payload.position,
    )
    for index, variant in enumerate(payload.variants):
        product.variants.append(
            ProductVariant(
                sku=variant.sku,
                name=variant.name,
                price_cents=variant.price_cents,
                compare_at_price_cents=variant.compare_at_price_cents,
                currency=variant.currency,
                inventory_quantity=variant.inventory_quantity,
                track_inventory=variant.track_inventory,
                weight_grams=variant.weight_grams,
                position=variant.position or index,
                is_active=variant.is_active,
            )
        )

    session.add(product)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "That product slug or SKU is already in use.",
            details={"slug": payload.slug},
        ) from exc

    await session.refresh(product, ["variants", "category"])
    return product


async def update_product(session: AsyncSession, product_id: UUID, payload: ProductPatch) -> Product:
    """Apply a partial update to a product.

    Only fields explicitly present in the request body are written.
    `exclude_unset` is what distinguishes "set description to null" from
    "do not touch the description" — without it, every omitted field would be
    overwritten with its default.

    Args:
        session: Active session.
        product_id: The product to update.
        payload: A validated `ProductPatch`.

    Returns:
        The updated product.

    Raises:
        NotFoundError: If the product does not exist.
        ConflictError: If the new slug is taken.
    """
    product = await session.get(Product, product_id)
    if product is None:
        raise NotFoundError("Product not found.")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, field, value)

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("That product slug is already in use.") from exc

    await session.refresh(product, ["variants", "category"])
    return product


async def replace_variants(
    session: AsyncSession, product_id: UUID, variants: list[VariantWrite]
) -> Product:
    """Replace a product's variant list wholesale.

    Variants are matched by SKU so that stock and IDs survive an edit. A naive
    delete-then-insert would orphan every order line pointing at the old
    variant IDs and reset inventory to whatever was typed into the form.

    Args:
        session: Active session.
        product_id: The product whose variants to replace.
        variants: Validated `VariantWrite` objects.

    Returns:
        The product with its new variant set.

    Raises:
        NotFoundError: If the product does not exist.
        ValidationFailedError: If the payload repeats a SKU.
    """
    product = await session.get(Product, product_id)
    if product is None:
        raise NotFoundError("Product not found.")
    await session.refresh(product, ["variants"])

    incoming_skus = [v.sku for v in variants]
    if len(set(incoming_skus)) != len(incoming_skus):
        raise ValidationFailedError("Each variant needs a distinct SKU.")

    existing = {v.sku: v for v in product.variants}

    for index, payload in enumerate(variants):
        if (current := existing.pop(payload.sku, None)) is not None:
            current.name = payload.name
            current.price_cents = payload.price_cents
            current.compare_at_price_cents = payload.compare_at_price_cents
            current.currency = payload.currency
            current.inventory_quantity = payload.inventory_quantity
            current.track_inventory = payload.track_inventory
            current.weight_grams = payload.weight_grams
            current.position = payload.position or index
            current.is_active = payload.is_active
        else:
            product.variants.append(
                ProductVariant(
                    sku=payload.sku,
                    name=payload.name,
                    price_cents=payload.price_cents,
                    compare_at_price_cents=payload.compare_at_price_cents,
                    currency=payload.currency,
                    inventory_quantity=payload.inventory_quantity,
                    track_inventory=payload.track_inventory,
                    weight_grams=payload.weight_grams,
                    position=payload.position or index,
                    is_active=payload.is_active,
                )
            )

    # Anything left in `existing` was not in the payload. Deactivate rather
    # than delete: past order lines reference these rows, and deleting them
    # would break order history.
    for removed in existing.values():
        removed.is_active = False

    await session.flush()
    await session.refresh(product, ["variants", "category"])
    return product


async def create_category(session: AsyncSession, payload: CategoryWrite) -> Category:
    """Create a category.

    Args:
        session: Active session.
        payload: A validated `CategoryWrite`.

    Returns:
        The created category.

    Raises:
        ConflictError: If the slug is taken.
    """
    category = Category(
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        position=payload.position,
        is_active=payload.is_active,
    )
    session.add(category)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("That category slug is already in use.") from exc
    return category
