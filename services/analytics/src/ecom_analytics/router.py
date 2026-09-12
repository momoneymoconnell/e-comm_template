"""Analytics routes: public event ingest and the admin dashboard."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from ecom_shared.identity import AdminUser, MaybeUser, require_admin
from ecom_shared.logging import get_logger
from ecom_shared.middleware import client_ip
from ecom_shared.schemas import ApiModel, Message
from fastapi import APIRouter, Depends, Header, Query, Request, status
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from ecom_analytics import service
from ecom_analytics.config import AnalyticsSettings
from ecom_analytics.duckdb_reader import DuckDBReader
from ecom_analytics.models import EVENT_TYPES

log = get_logger(__name__)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped database session."""
    async for session in request.app.state.db.session():
        yield session


def get_settings(request: Request) -> AnalyticsSettings:
    """Return this service's settings."""
    return request.app.state.settings  # type: ignore[no-any-return]


def get_duck(request: Request) -> DuckDBReader:
    """Return the shared DuckDB reader."""
    return request.app.state.duck  # type: ignore[no-any-return]


Db = Annotated[AsyncSession, Depends(get_db)]
Settings = Annotated[AnalyticsSettings, Depends(get_settings)]
Duck = Annotated[DuckDBReader, Depends(get_duck)]

#: Trailing window for dashboard queries, bounded so a single request cannot
#: ask the database to scan an unlimited range.
DaysQuery = Annotated[int, Query(ge=1, le=365)]


class TrackRequest(ApiModel):
    """One event reported by the storefront.

    Note what the client does **not** send: no IP, no timestamp, no user
    identity. Those are derived server-side, because a client-supplied value
    for any of them is trivially forged and would corrupt the data.
    """

    event_type: str = Field(description="One of the supported event types.")
    session_id: str | None = Field(default=None, max_length=100)
    path: str | None = Field(default=None, max_length=1000)
    referrer: str | None = Field(default=None, max_length=1000)
    properties: dict[str, Any] = Field(default_factory=dict)


router = APIRouter(prefix="/analytics", tags=["analytics"])
admin_router = APIRouter(
    prefix="/analytics/admin",
    tags=["analytics-admin"],
    dependencies=[Depends(require_admin)],
)


@router.post(
    "/events",
    response_model=Message,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Record an event",
)
async def track(
    payload: TrackRequest,
    request: Request,
    identity: MaybeUser,
    db: Db,
    settings: Settings,
    do_not_track: Annotated[str | None, Header(alias="DNT")] = None,
    country: Annotated[str | None, Header(alias="CF-IPCountry")] = None,
) -> Message:
    """Record a storefront event, stripped of identifying detail.

    Public and unauthenticated, because it must work for visitors who have not
    signed in. The gateway rate-limits it; the schema constrains what can be
    written; and the service hashes everything identifying before it is stored.

    **Do Not Track is honoured.** A browser sending ``DNT: 1`` gets a 202 and
    nothing is recorded. The signal costs one line to respect, and ignoring a
    visitor's explicit preference is hard to defend to them or to a regulator.

    An unrecognised event type is accepted and discarded rather than rejected.
    A frontend deploying a new event name before the backend knows about it is
    a normal ordering problem, and 4xx-ing analytics would fill the browser
    console with errors on an otherwise working site.
    """
    if do_not_track == "1":
        return Message(message="Not recorded (Do Not Track).")

    if payload.event_type not in EVENT_TYPES:
        log.info("analytics_unknown_event_ignored", event_type=payload.event_type[:40])
        return Message(message="Accepted.")

    ip = client_ip(request)
    await service.record_event(
        db,
        settings,
        event_type=payload.event_type,
        session_id=payload.session_id,
        ip=None if ip == "unknown" else ip,
        user_agent=request.headers.get("User-Agent"),
        path=payload.path,
        referrer=payload.referrer,
        country=country,
        user_id=identity.user_id if identity else None,
        # Bounded: properties is a free-form JSONB column on a public endpoint,
        # so an unbounded dict is an invitation to store megabytes per request.
        properties=dict(list(payload.properties.items())[:20]),
    )
    return Message(message="Accepted.")


@admin_router.get("/summary", summary="Traffic summary")
async def summary(admin: AdminUser, db: Db, days: DaysQuery = 30) -> dict[str, Any]:
    """Headline traffic figures for the dashboard."""
    return await service.traffic_summary(db, days=days)


@admin_router.get("/timeseries", summary="Daily traffic")
async def timeseries(admin: AdminUser, db: Db, days: DaysQuery = 30) -> list[dict[str, Any]]:
    """Daily page views and unique visitors, oldest first."""
    return await service.traffic_timeseries(db, days=days)


@admin_router.get("/top-pages", summary="Most-viewed pages")
async def pages(admin: AdminUser, db: Db, days: DaysQuery = 30) -> list[dict[str, Any]]:
    """The busiest paths in the window."""
    return await service.top_pages(db, days=days)


@admin_router.get("/funnel", summary="Checkout funnel")
async def funnel(admin: AdminUser, db: Db, days: DaysQuery = 30) -> list[dict[str, Any]]:
    """Distinct sessions reaching each step of the checkout funnel."""
    return await service.conversion_funnel(db, days=days)


@admin_router.get("/breakdown/{dimension}", summary="Break traffic down")
async def dimension_breakdown(
    dimension: str, admin: AdminUser, db: Db, days: DaysQuery = 30
) -> list[dict[str, Any]]:
    """Group sessions by device, browser, country or referrer.

    Args:
        dimension: ``device``, ``browser``, ``country`` or ``referrer``.
        admin: The acting administrator.
        db: Active session.
        days: Trailing window.

    Returns:
        Groups with session counts.

    Raises:
        ValidationFailedError: If the dimension is not recognised. The mapping
            is an allowlist, so no caller-supplied string ever reaches SQL.
    """
    from ecom_shared.errors import ValidationFailedError

    columns = {
        "device": "device_type",
        "browser": "browser_family",
        "country": "country",
        "referrer": "referrer_host",
    }
    column = columns.get(dimension)
    if column is None:
        raise ValidationFailedError(
            f"Unknown dimension: {dimension}", details={"available": sorted(columns)}
        )
    return await service.breakdown(db, column, days=days)


@admin_router.get("/revenue", summary="Revenue over time (DuckDB marts)")
async def revenue(admin: AdminUser, duck: Duck, days: DaysQuery = 30) -> dict[str, Any]:
    """Daily revenue from the dbt-built marts.

    Returns ``marts_available: false`` with empty data when dbt has not run
    yet, rather than erroring. The dashboard renders a "run `make dbt-build`"
    hint in that case, which is far more useful than a 500.
    """
    rows = await duck.revenue_by_day(days)
    return {"martsAvailable": duck.available, "days": days, "series": rows}


@admin_router.get("/top-products", summary="Best sellers (DuckDB marts)")
async def top_products(admin: AdminUser, duck: Duck, limit: int = 10) -> dict[str, Any]:
    """Best-selling products by revenue, from the marts."""
    rows = await duck.top_products(min(max(limit, 1), 50))
    return {"martsAvailable": duck.available, "products": rows}


@admin_router.get("/customers", summary="Customer value (DuckDB marts)")
async def customers(admin: AdminUser, duck: Duck) -> dict[str, Any]:
    """Aggregate customer lifetime value, from the marts."""
    return {"martsAvailable": duck.available, "summary": await duck.customer_summary()}
