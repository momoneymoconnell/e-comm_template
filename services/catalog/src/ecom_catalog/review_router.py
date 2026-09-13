"""HTTP routes for product reviews."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from ecom_shared.errors import NotFoundError
from ecom_shared.identity import AdminUser, CurrentUser, require_admin
from ecom_shared.schemas import Message, Page, PageParams
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ecom_catalog import reviews as review_lib
from ecom_catalog.config import CatalogSettings
from ecom_catalog.deps import get_db, get_settings
from ecom_catalog.models import Product, ProductReview
from ecom_catalog.schemas import (
    AdminReviewResponse,
    ModerateReviewRequest,
    RatingSummary,
    ReviewResponse,
    WriteReviewRequest,
)

public_router = APIRouter(prefix="/catalog/products", tags=["catalog-reviews"])
admin_router = APIRouter(
    prefix="/catalog/admin/reviews",
    tags=["catalog-admin"],
    dependencies=[Depends(require_admin)],
)

Db = Annotated[AsyncSession, Depends(get_db)]
Settings = Annotated[CatalogSettings, Depends(get_settings)]
PageQuery = Annotated[PageParams, Depends()]


async def _product_by_slug(db: AsyncSession, slug: str) -> Product:
    """Load an active product with its variants, or 404."""
    result = await db.execute(
        select(Product)
        .where(Product.slug == slug, Product.status == "active")
        .options(selectinload(Product.variants))
    )
    product = result.scalar_one_or_none()
    if product is None:
        raise NotFoundError("Product not found.")
    return product


@public_router.get(
    "/{slug}/reviews", response_model=Page[ReviewResponse], summary="Reviews for a product"
)
async def list_product_reviews(slug: str, db: Db, params: PageQuery) -> Page[ReviewResponse]:
    """Return published reviews, newest first.

    Public and unauthenticated. Hidden reviews are never included; the filter
    is applied in the query rather than in the response, so a moderated review
    cannot leak through a code path that forgets to check.
    """
    product = await _product_by_slug(db, slug)
    rows, total = await review_lib.list_reviews(
        db, product.id, offset=params.offset, limit=params.limit
    )
    items = [ReviewResponse.model_validate(row) for row in rows]
    return Page[ReviewResponse].build(items, total, params)


@public_router.get(
    "/{slug}/reviews/summary", response_model=RatingSummary, summary="Rating summary"
)
async def review_summary(slug: str, db: Db) -> RatingSummary:
    """Return the average rating and the per-star breakdown."""
    product = await _product_by_slug(db, slug)
    return RatingSummary(
        average=product.rating_average,
        count=product.rating_count,
        breakdown=await review_lib.rating_breakdown(db, product.id),
    )


@public_router.post(
    "/{slug}/reviews",
    response_model=ReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Write a review",
)
async def write_review(
    slug: str,
    payload: WriteReviewRequest,
    identity: CurrentUser,
    db: Db,
    settings: Settings,
) -> ReviewResponse:
    """Submit a review for a product.

    Requires an account and, by default, a verified purchase. One review per
    person per product, enforced by a unique constraint rather than a prior
    check, so two simultaneous submissions cannot both get through.
    """
    product = await _product_by_slug(db, slug)
    review = await review_lib.create_review(
        db,
        settings,
        product=product,
        user_id=identity.user_id,
        # The display name comes from the verified token rather than the
        # request body. Letting the client supply it would let anyone post as
        # "Verified Buyer" or impersonate another customer.
        author_name=(identity.email.split("@")[0] or "Customer"),
        rating=payload.rating,
        title=payload.title,
        body=payload.body,
    )
    return ReviewResponse.model_validate(review)


@public_router.get(
    "/{slug}/reviews/mine", response_model=ReviewResponse | None, summary="Your review"
)
async def my_review(slug: str, identity: CurrentUser, db: Db) -> ReviewResponse | None:
    """Return the caller's own review of this product, if they wrote one.

    Lets the UI show "you reviewed this" instead of offering a form that will
    be rejected by the one-per-person constraint.
    """
    product = await _product_by_slug(db, slug)
    result = await db.execute(
        select(ProductReview).where(
            ProductReview.product_id == product.id,
            ProductReview.user_id == identity.user_id,
        )
    )
    review = result.scalar_one_or_none()
    return ReviewResponse.model_validate(review) if review else None


# -----------------------------------------------------------------------------
# Moderation
# -----------------------------------------------------------------------------


@admin_router.get("", response_model=Page[AdminReviewResponse], summary="All reviews")
async def list_all_reviews(
    admin: AdminUser,
    db: Db,
    params: PageQuery,
    review_status: Annotated[
        str | None, Query(alias="status", pattern="^(published|hidden)$")
    ] = None,
) -> Page[AdminReviewResponse]:
    """Return every review, including hidden ones, newest first."""
    conditions = []
    if review_status:
        conditions.append(ProductReview.status == review_status)

    total = await db.scalar(select(func.count()).select_from(ProductReview).where(*conditions))
    result = await db.execute(
        select(ProductReview)
        .where(*conditions)
        .order_by(ProductReview.created_at.desc())
        .offset(params.offset)
        .limit(params.limit)
    )
    items = [AdminReviewResponse.model_validate(row) for row in result.scalars().all()]
    return Page[AdminReviewResponse].build(items, int(total or 0), params)


@admin_router.patch(
    "/{review_id}", response_model=AdminReviewResponse, summary="Publish or hide a review"
)
async def moderate_review(
    review_id: UUID, payload: ModerateReviewRequest, admin: AdminUser, db: Db
) -> AdminReviewResponse:
    """Change a review's visibility.

    Hiding a review also removes it from the product's average, so the rating
    on the page always reflects the reviews a shopper can actually read.
    """
    review = await review_lib.set_review_status(db, review_id, status=payload.status)
    return AdminReviewResponse.model_validate(review)


@admin_router.post("/recalculate", response_model=Message, summary="Rebuild rating totals")
async def recalculate(admin: AdminUser, db: Db) -> Message:
    """Recompute every product's rating counters from its reviews.

    A repair tool. The counters are denormalised for speed and are exactly the
    kind of thing that drifts after a bad migration or a manual database edit.
    """
    corrected = await review_lib.recalculate_ratings(db)
    return Message(message=f"Recalculated {corrected} product(s).")


__all__ = ["admin_router", "public_router"]
