"""Analytics business logic: privacy-preserving ingest and dashboard queries."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlparse

from ecom_shared.logging import get_logger
from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from ecom_analytics.config import AnalyticsSettings
from ecom_analytics.models import Event

log = get_logger(__name__)

#: Coarse device buckets, matched against a lowercased user agent in order.
_DEVICE_PATTERNS = (
    ("tablet", re.compile(r"ipad|tablet|playbook|silk")),
    ("mobile", re.compile(r"mobi|android|iphone|ipod|blackberry|windows phone")),
)

#: Coarse browser families. Order matters — Edge and Chrome both contain
#: "chrome" in their user agent strings, so the more specific one is tested
#: first.
_BROWSER_PATTERNS = (
    ("edge", re.compile(r"edg[ea]?/")),
    ("opera", re.compile(r"opr/|opera")),
    ("chrome", re.compile(r"chrome|crios")),
    ("firefox", re.compile(r"firefox|fxios")),
    ("safari", re.compile(r"safari")),
    ("bot", re.compile(r"bot|crawler|spider|curl|wget|python-requests")),
)


def current_salt(settings: AnalyticsSettings) -> str:
    """Derive today's hashing salt.

    Built from the JWT secret plus a rotating period number, so it is stable
    within a period and unrecoverable without the secret.

    **Why rotate at all.** A fixed salt means the same visitor hashes to the
    same value forever, which is a permanent pseudonymous identifier — exactly
    the thing GDPR treats as personal data. Rotating daily caps how long any
    visitor can be followed, while still answering the question a dashboard
    actually asks ("how many distinct people came today").

    Args:
        settings: Supplies the secret and the rotation period.

    Returns:
        The salt for the current period.
    """
    period = date.today().toordinal() // max(1, settings.ip_salt_rotation_days)
    return f"{settings.jwt_secret_key.get_secret_value()}:{period}"


def hash_visitor(ip: str | None, user_agent: str | None, salt: str) -> str:
    """Produce a rotating pseudonymous visitor identifier.

    The IP address itself is never stored — only this digest. SHA-256 over the
    IP alone would be trivially reversible (there are only ~4 billion IPv4
    addresses; a rainbow table is an afternoon's work), so the secret salt is
    what makes it genuinely one-way.

    Args:
        ip: The client IP, used and discarded.
        user_agent: Raw user agent, used and discarded.
        salt: From `current_salt`.

    Returns:
        A 64-character hex digest.
    """
    material = f"{salt}|{ip or 'unknown'}|{(user_agent or '')[:200]}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def hash_session(session_id: str | None, salt: str) -> str:
    """Hash the client-supplied session identifier.

    Args:
        session_id: Opaque per-tab identifier generated in the browser.
        salt: From `current_salt`.

    Returns:
        A 64-character hex digest.
    """
    return hashlib.sha256(f"{salt}|session|{session_id or 'none'}".encode()).hexdigest()


def classify_device(user_agent: str | None) -> tuple[str, str]:
    """Reduce a user agent to a coarse device and browser family.

    Deliberately imprecise. A full user agent string is close to unique per
    device and is one of the strongest browser-fingerprinting signals there is;
    "mobile / safari" tells the dashboard everything it needs and identifies
    nobody.

    Args:
        user_agent: The raw header value.

    Returns:
        ``(device_type, browser_family)``.
    """
    if not user_agent:
        return "unknown", "unknown"

    lowered = user_agent.lower()
    device = "desktop"
    for name, pattern in _DEVICE_PATTERNS:
        if pattern.search(lowered):
            device = name
            break

    browser = "other"
    for name, pattern in _BROWSER_PATTERNS:
        if pattern.search(lowered):
            browser = name
            break

    return device, browser


def clean_path(raw: str | None) -> str | None:
    """Strip the query string and cap the length of a reported path.

    Query strings routinely carry search terms, email addresses from
    click-through links and, occasionally, tokens. None of that belongs in an
    analytics table, and the path alone is what page-view reporting needs.

    Args:
        raw: The client-reported path or URL.

    Returns:
        A cleaned path, or ``None``.
    """
    if not raw:
        return None
    parsed = urlparse(raw)
    path = parsed.path or "/"
    return path[:500]


def referrer_host(raw: str | None) -> str | None:
    """Reduce a referrer to its hostname.

    The full referring URL can expose someone's search query or the private
    page they arrived from. The host — "google.com", "instagram.com" — is what
    acquisition reporting is actually about.

    Args:
        raw: The reported referrer.

    Returns:
        The hostname, or ``None``.
    """
    if not raw:
        return None
    try:
        host = urlparse(raw).hostname
    except ValueError:
        return None
    return host[:200] if host else None


async def record_event(
    session: AsyncSession,
    settings: AnalyticsSettings,
    *,
    event_type: str,
    session_id: str | None,
    ip: str | None,
    user_agent: str | None,
    path: str | None,
    referrer: str | None,
    country: str | None,
    user_id: Any | None,
    properties: dict[str, Any],
) -> Event:
    """Record one event, stripped of identifying detail.

    Args:
        session: Active session.
        settings: Supplies the salt.
        event_type: One of `EVENT_TYPES`; validated by the request schema.
        session_id: Client-generated session identifier.
        ip: Client IP, hashed and discarded.
        user_agent: Raw user agent, classified and discarded.
        path: Reported path; query string stripped.
        referrer: Reported referrer; reduced to a host.
        country: Two-letter country code from a CDN header, if any.
        user_id: The signed-in user, if any.
        properties: Event-specific structured data.

    Returns:
        The recorded event.
    """
    salt = current_salt(settings)
    device, browser = classify_device(user_agent)

    event = Event(
        event_type=event_type,
        session_hash=hash_session(session_id, salt),
        visitor_hash=hash_visitor(ip, user_agent, salt),
        user_id=user_id,
        path=clean_path(path),
        referrer_host=referrer_host(referrer),
        device_type=device,
        browser_family=browser,
        country=(country or "").upper()[:2] or None,
        properties=properties,
        # Server clock, deliberately. A client that misreports time would
        # otherwise corrupt every time series on the dashboard.
        occurred_at=datetime.now(UTC),
    )
    session.add(event)
    return event


# -----------------------------------------------------------------------------
# Dashboard queries
# -----------------------------------------------------------------------------


async def traffic_summary(session: AsyncSession, *, days: int = 30) -> dict[str, Any]:
    """Headline traffic figures for the dashboard.

    Args:
        session: Active session.
        days: Trailing window.

    Returns:
        Page views, unique visitors and sessions, for the window and for today.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    window = (
        await session.execute(
            select(
                func.count().filter(Event.event_type == "page_view").label("page_views"),
                func.count(func.distinct(Event.visitor_hash)).label("visitors"),
                func.count(func.distinct(Event.session_hash)).label("sessions"),
            ).where(Event.occurred_at >= since)
        )
    ).one()

    day = (
        await session.execute(
            select(
                func.count().filter(Event.event_type == "page_view").label("page_views"),
                func.count(func.distinct(Event.visitor_hash)).label("visitors"),
            ).where(Event.occurred_at >= today)
        )
    ).one()

    return {
        "windowDays": days,
        "pageViews": int(window.page_views),
        "uniqueVisitors": int(window.visitors),
        "sessions": int(window.sessions),
        "todayPageViews": int(day.page_views),
        "todayVisitors": int(day.visitors),
    }


async def traffic_timeseries(session: AsyncSession, *, days: int = 30) -> list[dict[str, Any]]:
    """Daily page views and visitors, oldest first.

    Grouping is done in Postgres with `date_trunc` rather than in Python: the
    database has an index on `occurred_at` and can aggregate without sending
    every row over the wire.

    Args:
        session: Active session.
        days: Trailing window.

    Returns:
        One entry per day that has data.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    day_column = func.date_trunc("day", Event.occurred_at).label("day")

    rows = (
        await session.execute(
            select(
                day_column,
                func.count().filter(Event.event_type == "page_view").label("page_views"),
                func.count(func.distinct(Event.visitor_hash)).label("visitors"),
            )
            .where(Event.occurred_at >= since)
            .group_by(day_column)
            .order_by(day_column)
        )
    ).all()

    by_day = {row.day.date(): (int(row.page_views), int(row.visitors)) for row in rows}

    # Zero-fill every day in the window.
    #
    # GROUP BY only returns days that have events, so a quiet Tuesday is simply
    # absent from the result. A chart drawn from that has no way to tell "no
    # traffic" from "no data" - it draws a straight line across the gap, which
    # reads as steady trading rather than none. Worse, a brand-new install has
    # exactly one day with data and the chart renders as a single bar filling
    # the entire width.
    #
    # Same reasoning as the date spine in the dbt models; see
    # analytics/dbt_ecom/models/marts/mart_traffic_daily.sql.
    today = datetime.now(UTC).date()
    series: list[dict[str, Any]] = []
    for offset in range(days, -1, -1):
        day = today - timedelta(days=offset)
        page_views, visitors = by_day.get(day, (0, 0))
        series.append({"day": day.isoformat(), "pageViews": page_views, "visitors": visitors})
    return series


async def top_pages(
    session: AsyncSession, *, days: int = 30, limit: int = 10
) -> list[dict[str, Any]]:
    """Most-viewed paths in the window.

    Args:
        session: Active session.
        days: Trailing window.
        limit: How many to return.

    Returns:
        Paths with view and visitor counts, busiest first.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await session.execute(
            select(
                Event.path,
                func.count().label("views"),
                func.count(func.distinct(Event.visitor_hash)).label("visitors"),
            )
            .where(
                Event.occurred_at >= since,
                Event.event_type == "page_view",
                Event.path.is_not(None),
            )
            .group_by(Event.path)
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()
    return [
        {"path": row.path, "views": int(row.views), "visitors": int(row.visitors)} for row in rows
    ]


async def conversion_funnel(session: AsyncSession, *, days: int = 30) -> list[dict[str, Any]]:
    """Count distinct sessions reaching each checkout step.

    Counted on **distinct sessions**, not raw events. A shopper who adds three
    items to a cart is one session that reached "add to cart", not three — and
    counting events would make the funnel widen partway down, which is
    nonsense.

    Args:
        session: Active session.
        days: Trailing window.

    Returns:
        One entry per funnel step, with a conversion rate relative to the top.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    steps = ("page_view", "product_view", "add_to_cart", "checkout_started", "checkout_completed")

    rows = (
        await session.execute(
            select(
                Event.event_type,
                func.count(func.distinct(Event.session_hash)).label("sessions"),
            )
            .where(Event.occurred_at >= since, Event.event_type.in_(steps))
            .group_by(Event.event_type)
        )
    ).all()

    counts = {row.event_type: int(row.sessions) for row in rows}
    top = counts.get("page_view", 0)

    return [
        {
            "step": step,
            "sessions": counts.get(step, 0),
            "rateFromTop": round(counts.get(step, 0) / top * 100, 2) if top else 0.0,
        }
        for step in steps
    ]


async def breakdown(
    session: AsyncSession, column: str, *, days: int = 30, limit: int = 10
) -> list[dict[str, Any]]:
    """Group sessions by device, browser, country or referrer host.

    Args:
        session: Active session.
        column: One of ``device_type``, ``browser_family``, ``country``,
            ``referrer_host``.
        days: Trailing window.
        limit: How many groups to return.

    Returns:
        Groups with session counts, largest first.

    Raises:
        ValueError: If `column` is not one of the four allowed names. The name
            is interpolated into a column reference, so it is checked against
            an allowlist rather than being passed through — a caller-supplied
            column name reaching SQL unchecked is an injection point.
    """
    allowed = {"device_type", "browser_family", "country", "referrer_host"}
    if column not in allowed:
        raise ValueError(f"breakdown column must be one of {sorted(allowed)}")

    target = getattr(Event, column)
    since = datetime.now(UTC) - timedelta(days=days)

    rows = (
        await session.execute(
            select(target, func.count(func.distinct(Event.session_hash)).label("sessions"))
            .where(Event.occurred_at >= since, target.is_not(None))
            .group_by(target)
            .order_by(func.count(func.distinct(Event.session_hash)).desc())
            .limit(limit)
        )
    ).all()
    return [{"value": row[0], "sessions": int(row.sessions)} for row in rows]


async def purge_old_events(session: AsyncSession, settings: AnalyticsSettings) -> int:
    """Delete raw events past the retention window.

    The dbt-built aggregates survive, so long-range reporting still works. What
    is discarded is the row-level detail, which has served its purpose once
    aggregated and is only a liability afterwards.

    Args:
        session: Active session.
        settings: Supplies the retention window.

    Returns:
        How many rows were deleted.
    """
    cutoff = datetime.now(UTC) - timedelta(days=settings.event_retention_days)
    result = await session.execute(delete(Event).where(Event.occurred_at < cutoff))
    deleted = cast("CursorResult[Any]", result).rowcount or 0
    if deleted:
        log.info("analytics_events_purged", deleted=deleted)
    return deleted
