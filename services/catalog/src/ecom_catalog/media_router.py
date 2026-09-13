"""Routes for uploading, serving and attaching product images.

Two audiences, deliberately split:

* `GET /catalog/media/{filename}` is **public**, because every visitor needs to
  load product photographs. It is read-only and serves nothing but bytes that
  this service wrote itself.
* Everything else requires an admin.
"""

from __future__ import annotations

from typing import Annotated, Any, cast
from uuid import UUID

from ecom_shared.errors import ConflictError, NotFoundError, ValidationFailedError
from ecom_shared.identity import AdminUser, require_admin
from ecom_shared.logging import get_logger
from ecom_shared.schemas import Message, Page, PageParams
from fastapi import APIRouter, Depends, File, Request, Response, UploadFile, status
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from ecom_catalog import media as media_lib
from ecom_catalog.config import CatalogSettings
from ecom_catalog.deps import get_db, get_settings
from ecom_catalog.models import MediaAsset, Product, ProductImage
from ecom_catalog.schemas import (
    AttachImageRequest,
    MediaResponse,
    ProductImageResponse,
    ReorderImagesRequest,
    UpdateImageRequest,
)

log = get_logger(__name__)

public_router = APIRouter(prefix="/catalog/media", tags=["catalog-media"])
admin_router = APIRouter(
    prefix="/catalog/admin/media",
    tags=["catalog-admin"],
    dependencies=[Depends(require_admin)],
)

Db = Annotated[AsyncSession, Depends(get_db)]
Settings = Annotated[CatalogSettings, Depends(get_settings)]
PageQuery = Annotated[PageParams, Depends()]


def _storage(request: Request) -> media_lib.LocalMediaStorage:
    """Return the shared storage backend, built once at startup."""
    return request.app.state.media  # type: ignore[no-any-return]


Storage = Annotated[media_lib.LocalMediaStorage, Depends(_storage)]


def _to_response(asset: MediaAsset) -> MediaResponse:
    """Build the API representation of a stored image."""
    return MediaResponse.model_validate(asset)


def image_to_response(image: ProductImage) -> ProductImageResponse:
    """Build the API representation of a gallery entry."""
    return ProductImageResponse.model_validate(image)


# -----------------------------------------------------------------------------
# Public: serving the files
# -----------------------------------------------------------------------------


@public_router.get("/{filename}", summary="Fetch an image")
async def serve_media(filename: str, storage: Storage) -> Response:
    """Return a stored image.

    Filenames are content hashes, so a given URL always refers to the same
    bytes. That makes the response safe to cache indefinitely, which is what
    `immutable` tells the browser and any CDN in front of it: never revalidate.
    Editing an image produces a new hash and therefore a new URL.

    Args:
        filename: The stored filename.
        storage: The media backend.

    Returns:
        The image bytes.

    Raises:
        NotFoundError: If no such file exists.
    """
    data = storage.read(filename)
    if data is None:
        raise NotFoundError("Image not found.")

    return Response(
        content=data,
        media_type=media_lib.content_type_for(filename),
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            # Belt and braces. The content type is derived from a filename this
            # service generated, but a browser that sniffs its way to something
            # executable would turn the media endpoint into a stored-XSS vector.
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


# -----------------------------------------------------------------------------
# Admin: the media library
# -----------------------------------------------------------------------------


@admin_router.post(
    "", response_model=MediaResponse, status_code=status.HTTP_201_CREATED, summary="Upload an image"
)
async def upload_media(
    admin: AdminUser,
    db: Db,
    storage: Storage,
    file: Annotated[UploadFile, File(description="JPEG, PNG or WebP, up to 8 MB.")],
) -> MediaResponse:
    """Accept an image, process it, and store it.

    The uploaded bytes are never written to disk as received. They are decoded,
    stripped of metadata, resized and re-encoded first - see `media.py` for why
    that matters.

    Uploading the same picture twice returns the existing record rather than
    creating a duplicate, because the filename is a hash of the processed
    bytes.
    """
    raw = await file.read()
    processed = media_lib.process_upload(raw, original_name=file.filename or "upload")

    existing = (
        await db.execute(select(MediaAsset).where(MediaAsset.filename == processed.filename))
    ).scalar_one_or_none()
    if existing is not None:
        log.info("media_upload_deduplicated", filename=processed.filename)
        return _to_response(existing)

    storage.write(processed.filename, processed.data, processed.content_type)
    storage.write(processed.thumb_filename, processed.thumb_data, processed.content_type)

    asset = MediaAsset(
        filename=processed.filename,
        thumb_filename=processed.thumb_filename,
        original_name=(file.filename or "")[:260] or None,
        content_type=processed.content_type,
        width=processed.width,
        height=processed.height,
        size_bytes=len(processed.data),
    )
    db.add(asset)
    await db.flush()

    log.info(
        "media_uploaded",
        media_id=str(asset.id),
        bytes_in=len(raw),
        bytes_out=len(processed.data),
        actor=str(admin.user_id),
    )
    return _to_response(asset)


@admin_router.get("", response_model=Page[MediaResponse], summary="Browse the media library")
async def list_media(admin: AdminUser, db: Db, params: PageQuery) -> Page[MediaResponse]:
    """Return uploaded images, newest first."""
    total = await db.scalar(select(func.count()).select_from(MediaAsset))
    result = await db.execute(
        select(MediaAsset)
        .order_by(MediaAsset.created_at.desc())
        .offset(params.offset)
        .limit(params.limit)
    )
    items = [_to_response(asset) for asset in result.scalars().all()]
    return Page[MediaResponse].build(items, int(total or 0), params)


@admin_router.delete("/{media_id}", response_model=Message, summary="Delete an image")
async def delete_media(media_id: UUID, admin: AdminUser, db: Db, storage: Storage) -> Message:
    """Delete an image and its file.

    Refuses while the image is still attached to a product. Deleting it anyway
    would leave gallery rows pointing at files that no longer exist, and a
    broken image on a product page is worse than a stale media library.

    Raises:
        NotFoundError: If the image does not exist.
        ConflictError: If it is still in use.
    """
    asset = await db.get(MediaAsset, media_id)
    if asset is None:
        raise NotFoundError("Image not found.")

    in_use = await db.scalar(
        select(func.count()).select_from(ProductImage).where(ProductImage.media_id == media_id)
    )
    if in_use:
        raise ConflictError(
            f"That image is used by {in_use} product(s). Remove it from them first.",
            details={"productCount": int(in_use)},
        )

    storage.delete(asset.filename)
    storage.delete(asset.thumb_filename)
    await db.delete(asset)

    log.info("media_deleted", media_id=str(media_id), actor=str(admin.user_id))
    return Message(message="Image deleted.")


# -----------------------------------------------------------------------------
# Admin: a product's gallery
# -----------------------------------------------------------------------------

gallery_router = APIRouter(
    prefix="/catalog/admin/products/{product_id}/images",
    tags=["catalog-admin"],
    dependencies=[Depends(require_admin)],
)


@gallery_router.get("", response_model=list[ProductImageResponse], summary="A product's gallery")
async def list_images(product_id: UUID, admin: AdminUser, db: Db) -> list[ProductImageResponse]:
    """Return a product's images in gallery order."""
    result = await db.execute(
        select(ProductImage)
        .where(ProductImage.product_id == product_id)
        .order_by(ProductImage.position)
    )
    return [image_to_response(image) for image in result.scalars().all()]


@gallery_router.post(
    "",
    response_model=ProductImageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an image to a product",
)
async def attach_image(
    product_id: UUID,
    payload: AttachImageRequest,
    admin: AdminUser,
    db: Db,
) -> ProductImageResponse:
    """Attach an uploaded image to a product, appended to the end of the gallery.

    Raises:
        NotFoundError: If the product or the image does not exist.
        ConflictError: If the image is already in this product's gallery.
    """
    product = await db.get(Product, product_id)
    if product is None:
        raise NotFoundError("Product not found.")

    asset = await db.get(MediaAsset, payload.media_id)
    if asset is None:
        raise NotFoundError("Image not found.")

    duplicate = await db.scalar(
        select(func.count())
        .select_from(ProductImage)
        .where(
            ProductImage.product_id == product_id,
            ProductImage.media_id == payload.media_id,
        )
    )
    if duplicate:
        raise ConflictError("That image is already on this product.")

    highest = await db.scalar(
        select(func.coalesce(func.max(ProductImage.position), -1)).where(
            ProductImage.product_id == product_id
        )
    )

    image = ProductImage(
        product_id=product_id,
        media_id=payload.media_id,
        alt=payload.alt,
        position=int(highest or -1) + 1,
    )
    db.add(image)
    await db.flush()
    await db.refresh(image, ["media"])

    return image_to_response(image)


@gallery_router.patch("/{image_id}", response_model=ProductImageResponse, summary="Edit alt text")
async def update_image(
    product_id: UUID,
    image_id: UUID,
    payload: UpdateImageRequest,
    admin: AdminUser,
    db: Db,
) -> ProductImageResponse:
    """Update an image's alternative text."""
    image = (
        await db.execute(
            select(ProductImage).where(
                ProductImage.id == image_id, ProductImage.product_id == product_id
            )
        )
    ).scalar_one_or_none()
    if image is None:
        raise NotFoundError("Image not found on this product.")

    image.alt = payload.alt
    await db.flush()
    await db.refresh(image, ["media"])
    return image_to_response(image)


@gallery_router.put(
    "/order", response_model=list[ProductImageResponse], summary="Reorder the gallery"
)
async def reorder_images(
    product_id: UUID,
    payload: ReorderImagesRequest,
    admin: AdminUser,
    db: Db,
) -> list[ProductImageResponse]:
    """Set the gallery order from a complete list of image IDs.

    The first image becomes the one shown in listings.

    Raises:
        ValidationFailedError: If the list does not match the product's images
            exactly. Accepting a partial list would silently leave the omitted
            images at whatever position they happened to hold.
    """
    result = await db.execute(select(ProductImage).where(ProductImage.product_id == product_id))
    images = {image.id: image for image in result.scalars().all()}

    if set(payload.image_ids) != set(images):
        raise ValidationFailedError(
            "The order must list every image on this product, exactly once.",
            details={"expected": len(images), "received": len(set(payload.image_ids))},
        )

    for position, image_id in enumerate(payload.image_ids):
        images[image_id].position = position

    await db.flush()
    ordered = sorted(images.values(), key=lambda image: image.position)
    for image in ordered:
        await db.refresh(image, ["media"])
    return [image_to_response(image) for image in ordered]


@gallery_router.delete(
    "/{image_id}", response_model=Message, summary="Remove an image from a product"
)
async def detach_image(product_id: UUID, image_id: UUID, admin: AdminUser, db: Db) -> Message:
    """Remove an image from a product's gallery.

    The underlying file is kept, so it stays available in the media library and
    on any other product using it.
    """
    result = await db.execute(
        sql_delete(ProductImage).where(
            ProductImage.id == image_id, ProductImage.product_id == product_id
        )
    )
    if not cast("CursorResult[Any]", result).rowcount:
        raise NotFoundError("Image not found on this product.")
    return Message(message="Image removed.")
