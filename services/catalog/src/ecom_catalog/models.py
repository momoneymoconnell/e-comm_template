"""Database tables for the product catalogue (schema ``catalog``).

The shape is the standard three-level e-commerce model, and it is worth being
explicit about why, because getting it wrong later is expensive:

    category → product → variant

A **product** is the thing a customer thinks they are buying ("Doric Column
Lamp"). A **variant** is the thing that actually has a price, a barcode and a
number in a warehouse ("Doric Column Lamp, Large, Brass"). Orders reference
variants, never products.

Collapsing the two — one row per sellable item — works right up until the first
product needs a second size, at which point every URL, image and review is
attached to the wrong level and the migration is painful. The extra table is
cheap insurance, and it costs nothing for a business that never uses variants:
such products simply have exactly one.

Money is stored as integer minor units (`price_cents`). See
`ecom_shared.schemas` for why floats are not an option.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from ecom_shared.db import build_metadata, utcnow_sql
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for the catalog service.

    Its metadata carries schema="catalog", so every table declared
    against this base is created inside that schema.
    """

    metadata = build_metadata("catalog")


#: Public path images are served under.
#:
#: A constant rather than a setting, deliberately. The URL is built in two
#: places - the ORM properties below and the media router - and a configurable
#: prefix means those two can disagree, which shows up as broken images only
#: for products that happen to go through the other code path. Serving media
#: through a CDN is a rewrite rule at the edge, not something the application
#: needs to know about.
MEDIA_URL_PREFIX = "/api/catalog/media"

#: Product lifecycle.
#:
#: ``draft``    — being worked on; invisible to customers.
#: ``active``   — on sale.
#: ``archived`` — withdrawn but retained, because past orders still reference
#:                it and a deleted product turns order history into dead links.
PRODUCT_STATUSES = ("draft", "active", "archived")


class Category(Base):
    """A grouping of products, used for navigation.

    Attributes:
        slug: URL-safe identifier. Stable and unique — it appears in links and
            in search-engine results, so it should outlive title changes.
        position: Manual sort order for the navigation menu. Explicit rather
            than alphabetical, because merchandising order is a business
            decision, not an alphabetical accident.
    """

    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql(), onupdate=func.now()
    )

    products: Mapped[list[Product]] = relationship(back_populates="category")

    __table_args__ = (CheckConstraint("slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="slug_format"),)


class Product(Base):
    """Something the storefront displays.

    Attributes:
        status: See `PRODUCT_STATUSES`. Only ``active`` products are visible on
            the storefront; the public list endpoint filters on this rather
            than relying on the frontend to hide them.
        category_id: ``SET NULL`` on delete — removing a category must not
            silently delete the products in it.
    """

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft")

    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL")
    )
    image_url: Mapped[str | None] = mapped_column(String(500))
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql(), onupdate=func.now()
    )

    category: Mapped[Category | None] = relationship(back_populates="products")
    variants: Mapped[list[ProductVariant]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductVariant.position",
    )
    images: Mapped[list[ProductImage]] = relationship(
        cascade="all, delete-orphan",
        order_by="ProductImage.position",
    )

    __table_args__ = (
        CheckConstraint(f"status IN {PRODUCT_STATUSES}", name="status_valid"),
        CheckConstraint("slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="slug_format"),
        # The storefront's main query is "active products in this category,
        # in merchandising order"; this index answers it directly.
        Index("ix_products_status_category", "status", "category_id", "position"),
    )


class ProductVariant(Base):
    """A specific, purchasable configuration of a product.

    This is what a cart line and an order line point at, and what holds stock.

    Attributes:
        sku: Stock-keeping unit. Unique across the catalogue and the identifier
            a warehouse or accountant will actually use.
        price_cents: Price in minor units. Non-negative, enforced by a
            constraint — a negative price is a refund-generating bug, so the
            database refuses it outright.
        compare_at_price_cents: The "was" price for a sale badge. Optional.
        inventory_quantity: Units on hand. Constrained ``>= 0``, which is what
            makes overselling impossible even under concurrent checkouts: two
            simultaneous purchases of the last item cannot both succeed,
            because the second UPDATE violates the constraint and its whole
            transaction rolls back.
    """

    __tablename__ = "product_variants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    compare_at_price_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="usd")

    inventory_quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: When true, the variant can be bought regardless of stock (services,
    #: digital goods, made to order).
    track_inventory: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    weight_grams: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql(), onupdate=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="variants")

    __table_args__ = (
        CheckConstraint("price_cents >= 0", name="price_non_negative"),
        CheckConstraint("inventory_quantity >= 0", name="inventory_non_negative"),
        CheckConstraint("char_length(currency) = 3", name="currency_iso_4217"),
        Index("ix_product_variants_product", "product_id", "position"),
    )

    @property
    def in_stock(self) -> bool:
        """Whether this variant can currently be bought."""
        if not self.track_inventory:
            return True
        return self.inventory_quantity > 0


class MediaAsset(Base):
    """One uploaded image.

    Attributes:
        filename: Content-addressed name on disk, derived from a hash of the
            processed bytes. Two uploads of the same picture produce the same
            name and therefore one file.
        original_name: What the file was called when it was uploaded. Kept for
            the admin media library only; never used as a path.
        width, height: Dimensions of the stored full-size image, so the
            frontend can reserve space and avoid layout shift.
    """

    __tablename__ = "media_assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    filename: Mapped[str] = mapped_column(String(140), nullable=False, unique=True)
    thumb_filename: Mapped[str] = mapped_column(String(140), nullable=False)
    original_name: Mapped[str | None] = mapped_column(String(260))
    content_type: Mapped[str] = mapped_column(String(60), nullable=False)

    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )

    __table_args__ = (
        CheckConstraint("width > 0 AND height > 0", name="dimensions_positive"),
        Index("ix_media_assets_created", "created_at"),
    )

    @property
    def url(self) -> str:
        """Public URL of the full-size image."""
        return f"{MEDIA_URL_PREFIX}/{self.filename}"

    @property
    def thumb_url(self) -> str:
        """Public URL of the thumbnail."""
        return f"{MEDIA_URL_PREFIX}/{self.thumb_filename}"


class ProductImage(Base):
    """An image attached to a product, in gallery order.

    Separate from `Product` because real listings need several photographs, and
    a single `image_url` column forces you to pick one. The first image by
    position is the one used in listings.

    Attributes:
        alt: Alternative text. Not decoration - a product page whose images have
            no alt text is unusable with a screen reader, and in several
            jurisdictions that is a legal exposure for a shop.
    """

    __tablename__ = "product_images"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    alt: Mapped[str | None] = mapped_column(String(300))
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )

    #: Eagerly joined, because a gallery entry is never useful without its
    #: file: every read needs the URL and the dimensions.
    media: Mapped[MediaAsset] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("product_id", "media_id", name="uq_product_images_product_id_media"),
        Index("ix_product_images_product_position", "product_id", "position"),
    )

    # These four proxy the joined media row so a gallery entry serialises
    # directly. Without them Pydantic cannot build the response from the ORM
    # object, and a product with any image at all fails with a validation
    # error rather than rendering.
    @property
    def url(self) -> str:
        """Public URL of the full-size image."""
        return self.media.url

    @property
    def thumb_url(self) -> str:
        """Public URL of the thumbnail."""
        return self.media.thumb_url

    @property
    def width(self) -> int:
        """Full-size width in pixels."""
        return self.media.width

    @property
    def height(self) -> int:
        """Full-size height in pixels."""
        return self.media.height
