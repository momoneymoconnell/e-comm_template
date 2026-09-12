"""Configuration for the analytics service."""

from __future__ import annotations

from ecom_shared.config import ServiceSettings


class AnalyticsSettings(ServiceSettings):
    """Settings for event ingest and the admin dashboards.

    Attributes:
        duckdb_path: File holding the marts dbt builds. Read-only from this
            service's point of view — dbt owns writing it.
        ip_salt_rotation_days: How often the IP-hashing salt changes. See
            `service.hash_ip` for why rotation matters.
        event_retention_days: How long raw events are kept before aggregation
            is all that remains.
    """

    service_name: str = "analytics"
    db_schema: str = "analytics"

    duckdb_path: str = "/data/analytics.duckdb"
    ip_salt_rotation_days: int = 1
    event_retention_days: int = 400
