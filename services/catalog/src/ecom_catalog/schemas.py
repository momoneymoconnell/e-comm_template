"""Request and response models for the catalog API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from ecom_shared.schemas import ApiModel
from pydantic import Field, field_validator

#: Slug rule, matching the database CHECK constraint. Duplicated deliberately:
#: the API rejects a bad slug with a helpful 422 instead of a 500 from a
#: constraint violation, while the constraint remains the real guarantee.
SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"


class VariantResponse(ApiModel):
    """A purchasable variant as the storefront sees it.

    `inventory_quantity` is **not** exposed publicly — only `in_stock`.
    Exact stock levels are competitive information, and they also enable
    inventory-probing attacks where a bot infers your sales rate.
    """

    id: UUID
    sku: str
    name: str
    price_cents: int
    compare_at_price_cents: int | None
    currency: str
    in_stock: bool
    position: int


class AdminVariantResponse(VariantResponse):
    """A variant as an admin sees it: adds the real stock figure."""

    inventory_quantity: int
    track_inventory: bool
    is_active: bool
    weight_grams: int | None


class CategorySummary(ApiModel):
    """A category, flattened for embedding in a product response."""

    id: UUID
    slug: str
    name: str


class ProductResponse(ApiModel):
    """A product with its variants, for the storefront."""

    id: UUID
    slug: str
    title: str
    subtitle: str | None
    description: str | None
    status: str
    image_url: str | None
    category: CategorySummary | None
    variants: list[VariantResponse]
    created_at: datetime

    @property
    def from_price_cents(self) -> int | None:
        """Cheapest variant price, for a "from $X" label."""
        prices = [v.price_cents for v in self.variants]
        return min(prices) if prices else None


class CategoryResponse(ApiModel):
    """A category, with how many active products it holds."""

    id: UUID
    slug: str
    name: str
    description: str | None
    position: int
    is_active: bool
    product_count: int = 0


# -----------------------------------------------------------------------------
# Admin write models
# -----------------------------------------------------------------------------


class VariantWrite(ApiModel):
    """Create or update a variant."""

    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    price_cents: int = Field(ge=0, le=100_000_000, description="Price in minor units.")
    compare_at_price_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    currency: str = Field(default="usd", min_length=3, max_length=3)
    inventory_quantity: int = Field(default=0, ge=0)
    track_inventory: bool = True
    weight_grams: int | None = Field(default=None, ge=0)
    position: int = 0
    is_active: bool = True

    @field_validator("currency")
    @classmethod
    def _lowercase_currency(cls, value: str) -> str:
        """Normalise to lowercase; Stripe's API expects lowercase ISO 4217."""
        return value.lower()


class ProductWrite(ApiModel):
    """Create a product, optionally with its variants."""

    slug: str = Field(min_length=1, max_length=160, pattern=SLUG_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    subtitle: str | None = Field(default=None, max_length=300)
    description: str | None = None
    status: str = Field(default="draft", pattern="^(draft|active|archived)$")
    category_id: UUID | None = None
    image_url: str | None = Field(default=None, max_length=500)
    position: int = 0
    variants: list[VariantWrite] = Field(default_factory=list)


class ProductPatch(ApiModel):
    """Partial update. Every field optional; omitted fields are untouched."""

    slug: str | None = Field(default=None, max_length=160, pattern=SLUG_PATTERN)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    subtitle: str | None = Field(default=None, max_length=300)
    description: str | None = None
    status: str | None = Field(default=None, pattern="^(draft|active|archived)$")
    category_id: UUID | None = None
    image_url: str | None = Field(default=None, max_length=500)
    position: int | None = None


class CategoryWrite(ApiModel):
    """Create or update a category."""

    slug: str = Field(min_length=1, max_length=120, pattern=SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    position: int = 0
    is_active: bool = True


# -----------------------------------------------------------------------------
# Internal (service-to-service) models
# -----------------------------------------------------------------------------


class VariantPriceRequest(ApiModel):
    """Ask the catalogue to price and validate a set of variants.

    Called by orders at checkout. Prices are **never** taken from the browser:
    a client that can set its own prices can buy anything for one cent, and
    this is the single most common serious e-commerce vulnerability.
    """

    variant_ids: list[UUID] = Field(min_length=1, max_length=100)


class VariantPriceItem(ApiModel):
    """Authoritative pricing and availability for one variant."""

    id: UUID
    sku: str
    name: str
    product_title: str
    product_slug: str
    price_cents: int
    currency: str
    is_active: bool
    track_inventory: bool
    inventory_quantity: int
    image_url: str | None


class InventoryLine(ApiModel):
    """One variant and a quantity, for a reserve or release call."""

    variant_id: UUID
    quantity: int = Field(ge=1, le=10_000)


class InventoryRequest(ApiModel):
    """Reserve or release stock for several variants at once."""

    lines: list[InventoryLine] = Field(min_length=1, max_length=100)
