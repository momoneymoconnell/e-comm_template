"""Analytics service entrypoint."""

from __future__ import annotations

from ecom_shared.app import create_service_app
from ecom_shared.logging import get_logger
from fastapi import FastAPI

from ecom_analytics.config import AnalyticsSettings
from ecom_analytics.duckdb_reader import DuckDBReader
from ecom_analytics.router import admin_router, router

log = get_logger(__name__)

settings = AnalyticsSettings()


async def _init_duckdb(app: FastAPI) -> None:
    """Attach the DuckDB marts reader.

    Constructing it never touches the filesystem, so a missing marts file
    cannot stop the service booting. The dashboard degrades to querying
    Postgres directly until `make dbt-build` has run.
    """
    app.state.duck = DuckDBReader(settings.duckdb_path)
    if not app.state.duck.available:
        log.info(
            "duckdb_marts_not_built",
            path=settings.duckdb_path,
            hint="Run `make dbt-build` to populate the analytics marts.",
        )


app = create_service_app(
    settings,
    routers=[router, admin_router],
    title="Analytics Service",
    description=(
        "Traffic event ingest and admin dashboards.\n\n"
        "No raw IP address, full user agent or query string is ever stored. "
        "Visitors are identified by a salted hash whose salt rotates daily, "
        "which supports visitor counting while capping how long anyone can be "
        "followed. Do Not Track is honoured.\n\n"
        "Heavy analytical queries are served from DuckDB marts built by dbt, "
        "so a dashboard refresh never competes with checkout for Postgres."
    ),
    on_startup=[_init_duckdb],
)
