"""FastAPI dependencies for the payments service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ecom_payments.config import PaymentSettings
from ecom_payments.stripe_gateway import StripeGateway


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped database session.

    Args:
        request: Injected by FastAPI.

    Yields:
        A session that commits on success and rolls back on error.
    """
    async for session in request.app.state.db.session():
        yield session


def get_settings(request: Request) -> PaymentSettings:
    """Return this service's settings."""
    return request.app.state.settings  # type: ignore[no-any-return]


def get_gateway(request: Request) -> StripeGateway:
    """Return the shared Stripe gateway.

    Built once at startup and stored on `app.state`. Constructing a
    `StripeClient` per request would rebuild its connection pool every time and
    discard TLS sessions, which is pure latency on the checkout path.
    """
    return request.app.state.stripe  # type: ignore[no-any-return]


Db = Annotated[AsyncSession, Depends(get_db)]
Settings = Annotated[PaymentSettings, Depends(get_settings)]
Gateway = Annotated[StripeGateway, Depends(get_gateway)]
