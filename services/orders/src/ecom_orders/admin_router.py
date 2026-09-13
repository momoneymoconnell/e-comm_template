"""Admin routes for the orders service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from ecom_shared.errors import ConflictError, NotFoundError
from ecom_shared.identity import AdminUser, require_admin
from ecom_shared.schemas import Page, PageParams
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ecom_orders import service
from ecom_orders.deps import Catalog, Db, Notifications
from ecom_orders.models import DiscountCode, Order
from ecom_orders.schemas import (
    DiscountCodeResponse,
    DiscountCodeWrite,
    OrderResponse,
    OrderSummary,
    UpdateOrderStatusRequest,
)

router = APIRouter(
    prefix="/orders/admin", tags=["orders-admin"], dependencies=[Depends(require_admin)]
)

PageQuery = Annotated[PageParams, Depends()]


@router.get("", response_model=Page[OrderSummary], summary="List all orders")
async def list_orders(
    admin: AdminUser,
    db: Db,
    params: PageQuery,
    order_status: Annotated[str | None, Query(alias="status", max_length=30)] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[OrderSummary]:
    """List every order, filterable by status and searchable by number or email."""
    orders, total = await service.list_orders(
        db, status=order_status, search=search, offset=params.offset, limit=params.limit
    )
    items = [
        OrderSummary(
            id=o.id,
            order_number=o.order_number,
            status=o.status,
            email=o.email,
            total_cents=o.total_cents,
            currency=o.currency,
            item_count=sum(i.quantity for i in o.items),
            created_at=o.created_at,
        )
        for o in orders
    ]
    return Page[OrderSummary].build(items, total, params)


def _with_tracking(order: Order) -> OrderResponse:
    """Serialise an order, filling in the computed tracking URL."""
    response = OrderResponse.model_validate(order)
    response.tracking_url = service.tracking_url_for(order.carrier, order.tracking_number)
    return response


@router.get("/{order_id}", response_model=OrderResponse, summary="Get any order")
async def get_order(order_id: UUID, admin: AdminUser, db: Db) -> OrderResponse:
    """Return any order, with its full status history."""
    order = await service.get_order(db, order_id, is_admin=True)
    return _with_tracking(order)


@router.patch(
    "/{order_id}/status", response_model=OrderResponse, summary="Change an order's status"
)
async def update_status(
    order_id: UUID,
    payload: UpdateOrderStatusRequest,
    admin: AdminUser,
    db: Db,
    catalog: Catalog,
    notifications: Notifications,
) -> OrderResponse:
    """Move an order to a new status, subject to the state machine.

    An illegal transition (refunded then fulfilled, say) is rejected with 409
    rather than silently applied. Cancelling an unpaid order also returns its
    reserved stock to the catalogue, which is the whole point of cancelling.
    """
    order = await service.get_order(db, order_id, is_admin=True)

    if payload.status == "cancelled" and order.status == "pending_payment":
        await catalog.release_inventory([(item.variant_id, item.quantity) for item in order.items])

    order = await service.transition_order(
        db,
        order,
        new_status=payload.status,
        note=payload.note,
        actor_user_id=admin.user_id,
        carrier=payload.carrier,
        tracking_number=payload.tracking_number,
    )

    # Tell the customer their parcel is on its way. Dispatched after the
    # transition, and failures are swallowed inside the client - an email
    # problem must not roll back a shipment that has physically happened.
    if payload.status == "fulfilled":
        await notifications.send(
            template="order_shipped",
            to=order.email,
            context={
                "orderNumber": order.order_number,
                "trackingUrl": service.tracking_url_for(order.carrier, order.tracking_number),
            },
        )

    return _with_tracking(order)


@router.get("/stats/summary", summary="Revenue and order statistics")
async def order_stats(admin: AdminUser, db: Db, days: int = 30) -> dict[str, Any]:
    """Return headline order and revenue figures for the dashboard.

    Revenue counts only orders that actually reached ``paid`` or beyond.
    Including pending or cancelled orders would flatter the number and make the
    dashboard useless for deciding anything.

    Args:
        admin: The acting administrator.
        db: Active session.
        days: Size of the trailing window for the "recent" figures.

    Returns:
        Totals, revenue, average order value and a per-status breakdown.
    """
    since = datetime.now(UTC) - timedelta(days=max(1, min(days, 365)))
    paid_statuses = ("paid", "fulfilled", "delivered")

    totals = (
        await db.execute(
            select(
                func.count().label("total_orders"),
                func.count().filter(Order.status.in_(paid_statuses)).label("paid_orders"),
                func.coalesce(
                    func.sum(Order.total_cents).filter(Order.status.in_(paid_statuses)), 0
                ).label("revenue_cents"),
                func.count().filter(Order.created_at >= since).label("recent_orders"),
                func.coalesce(
                    func.sum(Order.total_cents).filter(
                        Order.status.in_(paid_statuses), Order.created_at >= since
                    ),
                    0,
                ).label("recent_revenue_cents"),
            ).select_from(Order)
        )
    ).one()

    by_status = (
        await db.execute(
            select(Order.status, func.count()).group_by(Order.status).order_by(Order.status)
        )
    ).all()

    paid_orders = int(totals.paid_orders)
    revenue = int(totals.revenue_cents)
    return {
        "totalOrders": int(totals.total_orders),
        "paidOrders": paid_orders,
        "revenueCents": revenue,
        "averageOrderValueCents": revenue // paid_orders if paid_orders else 0,
        "recentOrders": int(totals.recent_orders),
        "recentRevenueCents": int(totals.recent_revenue_cents),
        "windowDays": days,
        "byStatus": {status: int(count) for status, count in by_status},
    }


# -----------------------------------------------------------------------------
# Discount codes
# -----------------------------------------------------------------------------

discounts_router = APIRouter(
    prefix="/orders/admin/discounts",
    tags=["orders-admin"],
    dependencies=[Depends(require_admin)],
)


@discounts_router.get("", response_model=Page[DiscountCodeResponse], summary="List discount codes")
async def list_discounts(admin: AdminUser, db: Db, params: PageQuery) -> Page[DiscountCodeResponse]:
    """Return every discount code, newest first."""
    total = await db.scalar(select(func.count()).select_from(DiscountCode))
    result = await db.execute(
        select(DiscountCode)
        .order_by(DiscountCode.created_at.desc())
        .offset(params.offset)
        .limit(params.limit)
    )
    items = [DiscountCodeResponse.model_validate(row) for row in result.scalars().all()]
    return Page[DiscountCodeResponse].build(items, int(total or 0), params)


@discounts_router.post(
    "", response_model=DiscountCodeResponse, status_code=201, summary="Create a code"
)
async def create_discount(
    payload: DiscountCodeWrite, admin: AdminUser, db: Db
) -> DiscountCodeResponse:
    """Create a discount code.

    Raises:
        ConflictError: If the code already exists. Codes are case-insensitive,
            so WELCOME10 and welcome10 collide, which is what a shopper typing
            it in lowercase expects.
    """
    discount = DiscountCode(
        code=payload.code.strip(),
        description=payload.description,
        kind=payload.kind,
        value=payload.value,
        min_subtotal_cents=payload.min_subtotal_cents,
        max_uses=payload.max_uses,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        is_active=payload.is_active,
    )
    db.add(discount)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("That code already exists.") from exc

    return DiscountCodeResponse.model_validate(discount)


@discounts_router.patch(
    "/{discount_id}", response_model=DiscountCodeResponse, summary="Enable or disable a code"
)
async def toggle_discount(
    discount_id: UUID, admin: AdminUser, db: Db, is_active: bool
) -> DiscountCodeResponse:
    """Turn a code on or off.

    Disabling rather than deleting: orders reference the code they used, and
    the redemption count is worth keeping for whoever wants to know whether the
    campaign worked.
    """
    discount = await db.get(DiscountCode, discount_id)
    if discount is None:
        raise NotFoundError("Discount code not found.")
    discount.is_active = is_active
    return DiscountCodeResponse.model_validate(discount)
