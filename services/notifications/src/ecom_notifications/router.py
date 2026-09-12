"""Routes for queueing and inspecting email."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated
from uuid import UUID

from ecom_shared.identity import AdminUser, ServiceCaller, require_admin
from ecom_shared.schemas import ApiModel, Page, PageParams
from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ecom_notifications import service
from ecom_notifications.config import NotificationSettings
from ecom_notifications.models import Notification
from ecom_notifications.templates_registry import TEMPLATES


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped database session."""
    async for session in request.app.state.db.session():
        yield session


def get_settings(request: Request) -> NotificationSettings:
    """Return this service's settings."""
    return request.app.state.settings  # type: ignore[no-any-return]


Db = Annotated[AsyncSession, Depends(get_db)]
Settings = Annotated[NotificationSettings, Depends(get_settings)]
PageQuery = Annotated[PageParams, Depends()]


class SendRequest(ApiModel):
    """Queue an email.

    Called by other services, never by a browser — an open endpoint that sends
    arbitrary mail from your domain is an open relay, and your domain's sending
    reputation would not survive the week.
    """

    template: str = Field(min_length=1, max_length=80)
    to: EmailStr
    context: dict = Field(default_factory=dict)


class NotificationResponse(ApiModel):
    """A queued or delivered message, for the admin console.

    The rendered bodies are omitted. An admin list view has no need for the
    full HTML of every receipt, and shipping it would put customer order
    contents into a response that is merely browsed.
    """

    id: UUID
    template: str
    to_email: str
    subject: str
    status: str
    attempts: int
    last_error: str | None
    sent_at: datetime | None
    created_at: datetime


router = APIRouter(prefix="/notifications", tags=["notifications"])
admin_router = APIRouter(
    prefix="/notifications/admin",
    tags=["notifications-admin"],
    dependencies=[Depends(require_admin)],
)


@router.post(
    "/send",
    response_model=NotificationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue an email",
)
async def send(
    payload: SendRequest, caller: ServiceCaller, db: Db, settings: Settings
) -> NotificationResponse:
    """Render a template and queue it for delivery.

    Returns 202, not 200: the message is accepted, not yet delivered. The
    background worker sends it moments later, so a slow or unavailable mail
    server never blocks the checkout that triggered it.
    """
    notification = await service.queue_notification(
        db,
        settings,
        template=payload.template,
        to_email=payload.to,
        context=payload.context,
    )
    return NotificationResponse.model_validate(notification)


@router.get("/templates", summary="List available templates")
async def list_templates(caller: ServiceCaller) -> dict[str, list[str]]:
    """Return the template names this service can render."""
    return {"templates": sorted(TEMPLATES)}


@admin_router.get("", response_model=Page[NotificationResponse], summary="Browse sent email")
async def list_notifications(
    admin: AdminUser,
    db: Db,
    params: PageQuery,
    delivery_status: Annotated[
        str | None, Query(alias="status", pattern="^(queued|sending|sent|failed)$")
    ] = None,
) -> Page[NotificationResponse]:
    """Return a page of messages, newest first."""
    conditions = []
    if delivery_status:
        conditions.append(Notification.status == delivery_status)

    total = await db.scalar(select(func.count()).select_from(Notification).where(*conditions))
    result = await db.execute(
        select(Notification)
        .where(*conditions)
        .order_by(Notification.created_at.desc())
        .offset(params.offset)
        .limit(params.limit)
    )
    items = [NotificationResponse.model_validate(n) for n in result.scalars().all()]
    return Page[NotificationResponse].build(items, int(total or 0), params)


@admin_router.get("/stats", summary="Delivery statistics")
async def notification_stats(admin: AdminUser, db: Db) -> dict[str, int]:
    """Return queue depth and delivery outcomes for the dashboard."""
    row = (
        await db.execute(
            select(
                func.count().label("total"),
                func.count().filter(Notification.status == "queued").label("queued"),
                func.count().filter(Notification.status == "sent").label("sent"),
                func.count().filter(Notification.status == "failed").label("failed"),
            ).select_from(Notification)
        )
    ).one()
    return {
        "total": int(row.total),
        "queued": int(row.queued),
        "sent": int(row.sent),
        "failed": int(row.failed),
    }
