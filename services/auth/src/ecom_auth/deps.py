"""FastAPI dependencies for the auth service.

These exist so handlers can ask for what they need (`db: Db`) instead of
reaching into `request.app.state` themselves. The indirection is what makes the
service testable: a test overrides `get_db` with a transaction that rolls back,
and every handler transparently uses it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from ecom_auth.config import AuthSettings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a database session scoped to this request.

    Commits when the handler returns, rolls back if it raises. See
    `ecom_shared.db.Database.session`.

    Args:
        request: Injected by FastAPI; carries the app and its state.

    Yields:
        The request's session.
    """
    async for session in request.app.state.db.session():
        yield session


def get_settings(request: Request) -> AuthSettings:
    """Return this service's settings.

    Args:
        request: Injected by FastAPI.

    Returns:
        The `AuthSettings` instance created at startup.
    """
    return request.app.state.settings  # type: ignore[no-any-return]
