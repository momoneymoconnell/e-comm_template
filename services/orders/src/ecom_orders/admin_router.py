"""Admin routes for the orders service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from ecom_shared.identity import AdminUser, require_admin
from ecom_shared.schemas import Page, PageParams
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from ecom_orders import service
from ecom_orders.deps import Catalog, Db
from ecom_orders.models import Order
from ecom_orders.schemas import OrderResponse, OrderSummary, UpdateOrderStatusRequest

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


@router.get("/{order_id}", response_model=OrderResponse, summary="Get any order")
async def get_order(order_id: UUID, admin: AdminUser, db: Db) -> OrderResponse:
    """Return any order, with its full status history."""
    order = await service.get_order(db, order_id, is_admin=True)
    return OrderResponse.model_validate(order)


@router.patch(
    "/{order_id}/status", response_model=OrderResponse, summary="Change an order's status"
)
async def update_status(
    order_id: UUID,
    payload: UpdateOrderStatusRequest,
    admin: AdminUser,
    db: Db,
    catalog: Catalog,
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
    )
    return OrderResponse.model_validate(order)


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
