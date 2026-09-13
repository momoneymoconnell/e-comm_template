"""Product reviews: writing, listing and moderating.

Two decisions worth stating, because both are trade-offs rather than obvious
right answers.

**Reviews require a verified purchase by default.** It costs a spammer a real
order to leave one, which is a far better filter than any heuristic, and it
makes every rating on the site mean something. The cost is fewer reviews, and
some shops would rather have volume - hence
`CatalogSettings.reviews_require_purchase`, which turns the check off.

**Ratings are stored as a running count and sum on the product.** Computing an
average with a correlated subquery would turn a 24-product grid into 24 extra
aggregates. Keeping a count and a *sum* rather than an average means the
arithmetic stays in integers; averaging averages is how a rating quietly drifts
away from the reviews behind it. Both columns are updated in the same
transaction as the review, and a CHECK constraint asserts they stay consistent.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from ecom_shared.errors import ConflictError, ForbiddenError, NotFoundError, UpstreamError
from ecom_shared.logging import get_logger, get_request_id
from ecom_shared.middleware import REQUEST_ID_HEADER
from ecom_shared.security import create_service_token
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ecom_catalog.config import CatalogSettings
from ecom_catalog.models import Product, ProductReview, ProductVariant

log = get_logger(__name__)


async def has_purchased(
    settings: CatalogSettings, *, user_id: UUID, variant_ids: list[UUID]
) -> bool:
    """Ask the orders service whether this customer bought any of these variants.

    Args:
        settings: Supplies the orders URL and the signing key.
        user_id: The prospective reviewer.
        variant_ids: Every variant of the product being reviewed.

    Returns:
        Whether a paid order exists.

    Raises:
        UpstreamError: If orders cannot be reached. Deliberately *not* treated
            as "no purchase": failing open would let anyone review anything
            whenever that service is down, and failing closed with a clear
            error is the honest outcome.
    """
    if not variant_ids:
        return False

    token = create_service_token(
        service_name="catalog",
        secret_key=settings.jwt_secret_key.get_secret_value(),
        ttl_seconds=60,
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
            response = await client.post(
                f"{settings.orders_url}/internal/orders/purchases/check",
                json={
                    "userId": str(user_id),
                    "variantIds": [str(v) for v in variant_ids],
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    REQUEST_ID_HEADER: get_request_id(),
                },
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("purchase_check_failed", error=str(exc))
        raise UpstreamError("Could not verify your purchase. Please try again.") from exc

    return bool(response.json().get("purchased"))


async def list_reviews(
    session: AsyncSession,
    product_id: UUID,
    *,
    offset: int = 0,
    limit: int = 20,
    include_hidden: bool = False,
) -> tuple[list[ProductReview], int]:
    """Return a page of reviews for a product, newest first.

    Args:
        session: Active session.
        product_id: The product.
        offset: SQL offset.
        limit: SQL limit.
        include_hidden: Admin views pass ``True``; the storefront never does.

    Returns:
        ``(reviews, total matching count)``.
    """
    conditions = [ProductReview.product_id == product_id]
    if not include_hidden:
        conditions.append(ProductReview.status == "published")

    total = await session.scalar(select(func.count()).select_from(ProductReview).where(*conditions))
    result = await session.execute(
        select(ProductReview)
        .where(*conditions)
        .order_by(ProductReview.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all()), int(total or 0)


async def rating_breakdown(session: AsyncSession, product_id: UUID) -> dict[int, int]:
    """Count published reviews at each star level.

    Powers the "5 stars: 12, 4 stars: 3" histogram, which tells a shopper far
    more than an average alone - a 4.0 from all fours is a different product
    from a 4.0 from fives and ones.

    Args:
        session: Active session.
        product_id: The product.

    Returns:
        A mapping of star value to count, with every level present.
    """
    rows = (
        await session.execute(
            select(ProductReview.rating, func.count())
            .where(
                ProductReview.product_id == product_id,
                ProductReview.status == "published",
            )
            .group_by(ProductReview.rating)
        )
    ).all()

    counts = dict.fromkeys(range(1, 6), 0)
    for rating, count in rows:
        counts[int(rating)] = int(count)
    return counts


async def create_review(
    session: AsyncSession,
    settings: CatalogSettings,
    *,
    product: Product,
    user_id: UUID,
    author_name: str,
    rating: int,
    title: str | None,
    body: str,
) -> ProductReview:
    """Write a review and fold it into the product's rating.

    Args:
        session: Active session.
        settings: Supplies the verified-purchase policy.
        product: The product being reviewed, with variants loaded.
        user_id: The reviewer.
        author_name: Display name, snapshotted onto the review.
        rating: One to five.
        title: Optional headline.
        body: The review text.

    Returns:
        The created review.

    Raises:
        ForbiddenError: If verification is required and they have not bought it.
        ConflictError: If they have already reviewed this product.
    """
    if settings.reviews_require_purchase:
        variant_ids = [variant.id for variant in product.variants]
        if not await has_purchased(settings, user_id=user_id, variant_ids=variant_ids):
            raise ForbiddenError(
                "You can review this once you have bought it.",
                details={"reason": "purchase_required"},
            )

    review = ProductReview(
        product_id=product.id,
        user_id=user_id,
        author_name=author_name[:120],
        rating=rating,
        title=(title or "").strip()[:160] or None,
        body=body.strip(),
        # Recorded as a fact about this review at the time of writing, rather
        # than recomputed on read: a refund later should not retroactively
        # strip the badge from an honest review.
        is_verified_purchase=settings.reviews_require_purchase,
        status="published",
    )
    session.add(review)

    # Folded in here, in the same transaction, so the product's rating can
    # never disagree with the reviews behind it.
    product.rating_count += 1
    product.rating_sum += rating

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "You have already reviewed this product. Edit your existing review instead."
        ) from exc

    log.info(
        "review_created",
        product_id=str(product.id),
        rating=rating,
        verified=review.is_verified_purchase,
    )
    return review


async def set_review_status(
    session: AsyncSession, review_id: UUID, *, status: str
) -> ProductReview:
    """Publish or hide a review, adjusting the product's rating to match.

    Hiding a review must remove it from the average as well as from the page.
    Leaving the totals alone would show a rating derived partly from reviews
    nobody can read.

    Args:
        session: Active session.
        review_id: The review to change.
        status: ``"published"`` or ``"hidden"``.

    Returns:
        The updated review.

    Raises:
        NotFoundError: If the review does not exist.
    """
    review = await session.get(ProductReview, review_id)
    if review is None:
        raise NotFoundError("Review not found.")

    if review.status == status:
        return review

    product = await session.get(Product, review.product_id)
    if product is not None:
        if status == "hidden":
            product.rating_count = max(0, product.rating_count - 1)
            product.rating_sum = max(0, product.rating_sum - review.rating)
        else:
            product.rating_count += 1
            product.rating_sum += review.rating

    review.status = status
    await session.flush()

    log.info("review_status_changed", review_id=str(review_id), status=status)
    return review


async def recalculate_ratings(session: AsyncSession) -> int:
    """Rebuild every product's rating totals from its reviews.

    A repair tool, not part of any request path. Denormalised counters are
    fast and are exactly the kind of thing that drifts after a bad migration or
    a manual database edit; having a one-command way to make them true again is
    worth the twenty lines.

    Args:
        session: Active session.

    Returns:
        How many products were corrected.
    """
    totals = (
        await session.execute(
            select(
                ProductReview.product_id,
                # Labelled `review_count`, not `count`. A SQLAlchemy Row is a
                # named tuple, so a label that collides with a tuple method -
                # `count` and `index` both do - resolves to the method instead
                # of the column, and the value is silently a bound method.
                func.count().label("review_count"),
                func.coalesce(func.sum(ProductReview.rating), 0).label("rating_total"),
            )
            .where(ProductReview.status == "published")
            .group_by(ProductReview.product_id)
        )
    ).all()
    by_product: dict[Any, tuple[int, int]] = {
        row.product_id: (int(row.review_count), int(row.rating_total)) for row in totals
    }

    products = (await session.execute(select(Product))).scalars().all()
    corrected = 0
    for product in products:
        count, total = by_product.get(product.id, (0, 0))
        if product.rating_count != count or product.rating_sum != total:
            product.rating_count = count
            product.rating_sum = total
            corrected += 1

    await session.flush()
    if corrected:
        log.warning("ratings_recalculated", products_corrected=corrected)
    return corrected


async def variants_for(session: AsyncSession, product_id: UUID) -> list[ProductVariant]:
    """Return a product's variant IDs, for the purchase check.

    Args:
        session: Active session.
        product_id: The product.

    Returns:
        Its variants.
    """
    result = await session.execute(
        select(ProductVariant).where(ProductVariant.product_id == product_id)
    )
    return list(result.scalars().all())
