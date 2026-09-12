"""FastAPI dependencies for the orders service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ecom_orders.clients import CatalogClient, NotificationsClient, PaymentsClient
from ecom_orders.config import OrderSettings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped database session.

    Args:
        request: Injected by FastAPI.

    Yields:
        A session that commits on success and rolls back on error.
    """
    async for session in request.app.state.db.session():
        yield session


def get_settings(request: Request) -> OrderSettings:
    """Return this service's settings.

    Args:
        request: Injected by FastAPI.

    Returns:
        The `OrderSettings` created at startup.
    """
    return request.app.state.settings  # type: ignore[no-any-return]


def get_catalog_client(
    settings: Annotated[OrderSettings, Depends(get_settings)],
) -> CatalogClient:
    """Provide a catalogue client.

    Injected rather than constructed inline so a test can override it with a
    fake and exercise checkout without a running catalog service.
    """
    return CatalogClient(settings)


def get_payments_client(
    settings: Annotated[OrderSettings, Depends(get_settings)],
) -> PaymentsClient:
    """Provide a payments client."""
    return PaymentsClient(settings)


def get_notifications_client(
    settings: Annotated[OrderSettings, Depends(get_settings)],
) -> NotificationsClient:
    """Provide a notifications client."""
    return NotificationsClient(settings)


Db = Annotated[AsyncSession, Depends(get_db)]
Settings = Annotated[OrderSettings, Depends(get_settings)]
Catalog = Annotated[CatalogClient, Depends(get_catalog_client)]
Payments = Annotated[PaymentsClient, Depends(get_payments_client)]
Notifications = Annotated[NotificationsClient, Depends(get_notifications_client)]
