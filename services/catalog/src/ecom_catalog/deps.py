"""FastAPI dependencies for the catalog service."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from ecom_catalog.config import CatalogSettings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped database session.

    Args:
        request: Injected by FastAPI.

    Yields:
        A session that commits on success and rolls back on error.
    """
    async for session in request.app.state.db.session():
        yield session


def get_settings(request: Request) -> CatalogSettings:
    """Return this service's settings.

    Args:
        request: Injected by FastAPI.

    Returns:
        The `CatalogSettings` created at startup.
    """
    return request.app.state.settings  # type: ignore[no-any-return]
