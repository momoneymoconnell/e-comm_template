"""Service-to-service routes. Not reachable from a browser.

The gateway does not proxy ``/internal/*`` at all, and every route here
additionally requires a service token. Two independent controls, because
relying on the proxy configuration alone means one mistaken route rule exposes
pricing and inventory mutation to the public internet.

This is where the **authoritative prices** come from. Orders never trusts the
browser for a price; it asks here.
"""

from __future__ import annotations

from typing import Annotated

from ecom_shared.identity import ServiceCaller
from ecom_shared.logging import get_logger
from ecom_shared.schemas import Message
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ecom_catalog import service
from ecom_catalog.deps import get_db
from ecom_catalog.schemas import (
    InventoryRequest,
    VariantPriceItem,
    VariantPriceRequest,
)

log = get_logger(__name__)

router = APIRouter(prefix="/internal/catalog", tags=["internal"])

Db = Annotated[AsyncSession, Depends(get_db)]


@router.post(
    "/variants/price",
    response_model=list[VariantPriceItem],
    summary="Authoritative pricing for a set of variants",
)
async def price_variants(
    payload: VariantPriceRequest, caller: ServiceCaller, db: Db
) -> list[VariantPriceItem]:
    """Return the real price and availability of each requested variant.

    Called by orders during checkout. The cart the browser submits contains
    variant IDs and quantities only — never prices. Those come from here, so a
    tampered cart cannot change what the customer is charged.

    Variants that do not exist are simply omitted; the caller compares the
    returned IDs against what it asked for and fails the checkout if any are
    missing.
    """
    variants = await service.get_variants(db, payload.variant_ids)
    return [
        VariantPriceItem(
            id=v.id,
            sku=v.sku,
            name=v.name,
            product_title=v.product.title,
            product_slug=v.product.slug,
            price_cents=v.price_cents,
            currency=v.currency,
            is_active=v.is_active and v.product.status == "active",
            track_inventory=v.track_inventory,
            inventory_quantity=v.inventory_quantity,
            image_url=v.product.image_url,
        )
        for v in variants
    ]


@router.post("/inventory/reserve", response_model=Message, summary="Reserve stock")
async def reserve_inventory(payload: InventoryRequest, caller: ServiceCaller, db: Db) -> Message:
    """Decrement stock for a set of variants, atomically.

    Either every line succeeds or none does — they share one transaction. See
    `service.reserve_inventory` for how the race between two shoppers buying
    the last unit is prevented.

    Raises:
        ConflictError: If any line has insufficient stock, naming the SKU.
    """
    await service.reserve_inventory(
        db, [(line.variant_id, line.quantity) for line in payload.lines]
    )
    log.info("inventory_reserve_requested", caller=caller, lines=len(payload.lines))
    return Message(message="Reserved.")


@router.post("/inventory/release", response_model=Message, summary="Release stock")
async def release_inventory(payload: InventoryRequest, caller: ServiceCaller, db: Db) -> Message:
    """Return previously reserved stock, after a cancelled or failed order."""
    await service.release_inventory(
        db, [(line.variant_id, line.quantity) for line in payload.lines]
    )
    log.info("inventory_release_requested", caller=caller, lines=len(payload.lines))
    return Message(message="Released.")
