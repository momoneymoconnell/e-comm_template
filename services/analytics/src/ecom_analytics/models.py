"""Database tables for traffic analytics (schema ``analytics``).

**Privacy is a design constraint here, not a feature.** Analytics tables are
where e-commerce systems most often accumulate personal data nobody decided to
collect, and then keep it forever.

What this schema stores and does not:

* **No raw IP addresses.** An IP address is personal data under GDPR. What the
  dashboard actually needs is "were these two page views the same visitor",
  which a salted, rotating hash answers without retaining the address itself.
* **No full user agent.** Parsed into a coarse device and browser family. The
  full string is a strong fingerprinting vector — it can be near-unique — and
  "was this mobile or desktop" is all a traffic dashboard needs.
* **No query strings.** Paths are stored without them, because query strings
  routinely contain search terms, email addresses from click-through links,
  and occasionally session tokens.
* **A finite retention window.** Raw events expire; the aggregates dbt builds
  from them do not.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from ecom_shared.db import build_metadata, utcnow_sql
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for the analytics service.

    Its metadata carries schema="analytics", so every table declared
    against this base is created inside that schema.
    """

    metadata = build_metadata("analytics")


#: Events the storefront is allowed to report.
#:
#: A closed list, not free text. An open `event_type` column fills up with
#: typos ("page_veiw"), becomes impossible to aggregate, and lets anyone who
#: finds the endpoint write arbitrary strings into your warehouse.
EVENT_TYPES = (
    "page_view",
    "product_view",
    "add_to_cart",
    "remove_from_cart",
    "checkout_started",
    "checkout_completed",
    "search",
    "signup",
    "login",
)


class Event(Base):
    """One recorded visitor action.

    Attributes:
        session_hash: Salted hash identifying a browsing session. Lets the
            dashboard count visitors and follow a funnel without storing
            anything that identifies a person.
        visitor_hash: Salted hash of the IP and coarse user agent, rotated
            daily. Distinguishes returning visitors *within* a day; after the
            salt rotates the same person hashes differently, which deliberately
            caps how long anyone can be tracked.
        user_id: Present only when the visitor is signed in — at which point
            they have an account and the association is one they made
            themselves.
        path: URL path with the query string stripped.
        properties: Event-specific structured data (product slug, search term
            length, cart value). JSONB so a new event type needs no migration.
    """

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)

    session_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    visitor_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    path: Mapped[str | None] = mapped_column(String(500))
    referrer_host: Mapped[str | None] = mapped_column(String(200))

    #: Coarse buckets only: "mobile"/"tablet"/"desktop", "chrome"/"safari"/...
    device_type: Mapped[str | None] = mapped_column(String(20))
    browser_family: Mapped[str | None] = mapped_column(String(40))
    #: Two-letter country from a CDN header when present. Country is coarse
    #: enough not to identify anyone, and it is what a revenue dashboard
    #: actually uses.
    country: Mapped[str | None] = mapped_column(String(2))

    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")

    #: Client-reported timestamp is never trusted for ordering; the server
    #: clock is authoritative. A client that lies about time would otherwise
    #: corrupt every time series on the dashboard.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )

    __table_args__ = (
        CheckConstraint(f"event_type IN {EVENT_TYPES}", name="event_type_valid"),
        # Every dashboard query is "events of type X in a time range", so the
        # index is ordered to match.
        Index("ix_events_type_time", "event_type", "occurred_at"),
        Index("ix_events_time", "occurred_at"),
        Index("ix_events_session", "session_hash", "occurred_at"),
    )


class DailyRollup(Base):
    """Pre-aggregated daily traffic, kept after raw events expire.

    Raw events are deleted at `event_retention_days`. Without this table, last
    year's traffic would simply vanish, which makes year-on-year comparison —
    the one number a retail business genuinely cares about — impossible.

    dbt populates this from the raw events; see ``analytics/dbt_ecom``.
    """

    __tablename__ = "daily_rollups"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    day: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    unique_sessions: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    unique_visitors: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=utcnow_sql()
    )

    __table_args__ = (Index("ix_daily_rollups_day_type", "day", "event_type", unique=True),)
