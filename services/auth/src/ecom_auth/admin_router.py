"""Admin-only routes: browse users, change their status, read the audit trail.

Every route here depends on `require_admin`, so the authorisation check cannot
be forgotten on a new endpoint — it is attached to the router, not repeated per
handler.

Admin reads of customer data are themselves audited. Watching the watchers is
standard practice in any system holding personal data, and it is what lets you
answer "who looked at this customer's record" after the fact.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from ecom_shared.identity import AdminUser, require_admin
from ecom_shared.middleware import client_ip
from ecom_shared.schemas import Page, PageParams
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select

from ecom_auth import service
from ecom_auth.deps import get_db, get_settings
from ecom_auth.models import AuditEvent, User
from ecom_auth.router import Db, Settings
from ecom_auth.schemas import AdminUpdateUserRequest, AuditEventResponse, UserResponse

router = APIRouter(
    prefix="/auth/admin",
    tags=["auth-admin"],
    # Applied to every route below. A new endpoint is protected by default,
    # which is the correct direction for a mistake to fall.
    dependencies=[Depends(require_admin)],
)

PageQuery = Annotated[PageParams, Depends()]


@router.get("/users", response_model=Page[UserResponse], summary="List users")
async def list_users(
    admin: AdminUser,
    db: Db,
    params: PageQuery,
    search: Annotated[str | None, Query(max_length=200)] = None,
    role: Annotated[str | None, Query(pattern="^(customer|admin)$")] = None,
    is_active: bool | None = None,
) -> Page[UserResponse]:
    """Return a page of users, with optional filters.

    The search term is bound as a query parameter, never concatenated into SQL.
    SQLAlchemy's `ilike` produces a placeholder, so a search for
    ``'; DROP TABLE users; --`` looks for a customer with that literal name and
    finds nobody.

    Args:
        admin: The acting administrator.
        db: Active session.
        params: Pagination.
        search: Substring match against email or name.
        role: Filter by role.
        is_active: Filter by active status.

    Returns:
        A page of users.
    """
    conditions = []
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(or_(User.email.ilike(pattern), User.full_name.ilike(pattern)))
    if role:
        conditions.append(User.role == role)
    if is_active is not None:
        conditions.append(User.is_active.is_(is_active))

    total = await db.scalar(select(func.count()).select_from(User).where(*conditions))
    result = await db.execute(
        select(User)
        .where(*conditions)
        .order_by(User.created_at.desc())
        .offset(params.offset)
        .limit(params.limit)
    )
    users = [UserResponse.model_validate(row) for row in result.scalars().all()]
    return Page[UserResponse].build(users, int(total or 0), params)


@router.get("/users/{user_id}", response_model=UserResponse, summary="Get one user")
async def get_user(user_id: UUID, admin: AdminUser, db: Db, request: Request) -> UserResponse:
    """Return a single user, recording that an admin viewed them."""
    user = await service.get_user_by_id(db, user_id)
    service.record_audit(
        db,
        action="admin.user_viewed",
        actor_user_id=admin.user_id,
        target_type="user",
        target_id=str(user_id),
        ip_address=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return UserResponse.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserResponse, summary="Update a user")
async def update_user(
    user_id: UUID,
    payload: AdminUpdateUserRequest,
    admin: AdminUser,
    db: Db,
    settings: Settings,
    request: Request,
) -> UserResponse:
    """Change a user's role or active status.

    See `service.admin_update_user` for the guard rails: no self-modification,
    promotions restricted to the ``ADMIN_EMAILS`` allowlist, and immediate
    session revocation on any change.
    """
    ip = client_ip(request)
    user = await service.admin_update_user(
        db,
        settings,
        actor=admin.user_id,
        target_user_id=user_id,
        is_active=payload.is_active,
        role=payload.role,
        ip_address=None if ip == "unknown" else ip,
        user_agent=request.headers.get("User-Agent"),
    )
    return UserResponse.model_validate(user)


@router.get("/audit", response_model=Page[AuditEventResponse], summary="Read the audit trail")
async def list_audit_events(
    admin: AdminUser,
    db: Db,
    params: PageQuery,
    action: Annotated[str | None, Query(max_length=100)] = None,
    actor_user_id: UUID | None = None,
) -> Page[AuditEventResponse]:
    """Return a page of audit events, newest first.

    Args:
        admin: The acting administrator.
        db: Active session.
        params: Pagination.
        action: Exact-match filter on the action verb.
        actor_user_id: Filter to one actor.

    Returns:
        A page of audit events.
    """
    conditions = []
    if action:
        conditions.append(AuditEvent.action == action)
    if actor_user_id:
        conditions.append(AuditEvent.actor_user_id == actor_user_id)

    total = await db.scalar(select(func.count()).select_from(AuditEvent).where(*conditions))
    result = await db.execute(
        select(AuditEvent)
        .where(*conditions)
        .order_by(AuditEvent.created_at.desc())
        .offset(params.offset)
        .limit(params.limit)
    )
    events = [AuditEventResponse.model_validate(row) for row in result.scalars().all()]
    return Page[AuditEventResponse].build(events, int(total or 0), params)


@router.get("/stats", summary="Account statistics for the dashboard")
async def user_stats(admin: AdminUser, db: Db) -> dict[str, int]:
    """Return headline account counts.

    Computed as one grouped query rather than four `COUNT(*)` round trips.

    Returns:
        Totals for all, active, admin and recently-registered accounts.
    """
    from datetime import UTC, datetime, timedelta

    week_ago = datetime.now(UTC) - timedelta(days=7)
    row = (
        await db.execute(
            select(
                func.count().label("total"),
                func.count().filter(User.is_active.is_(True)).label("active"),
                func.count().filter(User.role == "admin").label("admins"),
                func.count().filter(User.created_at >= week_ago).label("new_this_week"),
            ).select_from(User)
        )
    ).one()
    return {
        "total": int(row.total),
        "active": int(row.active),
        "admins": int(row.admins),
        "newThisWeek": int(row.new_this_week),
    }


__all__ = ["get_db", "get_settings", "router"]
