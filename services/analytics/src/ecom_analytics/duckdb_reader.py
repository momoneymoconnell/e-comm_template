"""Read the dbt-built marts out of DuckDB.

**Why DuckDB is here at all.** Postgres is the right store for transactions —
concurrent writes, row locks, foreign keys. It is not the right engine for
"scan every event of the last year and group it four ways", which is a column
scan over millions of rows. Running that on the production database competes
for the same buffer pool and connections that checkout needs.

DuckDB is the opposite trade: a columnar, embedded, read-mostly engine that
answers analytical queries in milliseconds and runs in-process with no server
to operate. dbt builds the marts into a file; this module reads them.

The fallback matters as much as the feature. If the file does not exist yet —
a fresh install, before the first `make dbt-build` — every function here
returns empty rather than raising, and the dashboard falls back to querying
Postgres directly. An analytics dependency must never be able to take the admin
console down.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import duckdb
from ecom_shared.logging import get_logger

log = get_logger(__name__)


class DuckDBReader:
    """Read-only access to the analytics marts.

    Attributes:
        path: Location of the DuckDB file dbt writes.
    """

    def __init__(self, path: str) -> None:
        """Store the file path. No connection is opened yet.

        Args:
            path: Path to the DuckDB database file.
        """
        self.path = Path(path)

    @property
    def available(self) -> bool:
        """Whether the marts file exists and is non-empty.

        Checked before every query so a missing file degrades to the Postgres
        fallback instead of raising.
        """
        try:
            return self.path.is_file() and self.path.stat().st_size > 0
        except OSError:
            return False

    def _query_sync(self, sql: str, params: list[Any] | None = None) -> list[dict]:
        """Run a query, opening and closing a read-only connection.

        Opening per query is deliberate. dbt rebuilds this file wholesale, and
        a long-lived handle would either block the rebuild or keep serving a
        stale, deleted version of the file. Connecting to a local file is
        sub-millisecond, so there is nothing to optimise here.

        Args:
            sql: The query.
            params: Positional parameters, bound rather than interpolated.

        Returns:
            Rows as dicts, or ``[]`` if the file is missing or the query fails.
        """
        if not self.available:
            return []

        try:
            # read_only stops this service from ever writing to a file dbt
            # owns, and lets several readers share it safely.
            with duckdb.connect(str(self.path), read_only=True) as conn:
                cursor = conn.execute(sql, params or [])
                columns = [d[0] for d in cursor.description or []]
                return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        except duckdb.Error as exc:
            # A missing table means dbt has not built that model yet, which is
            # a normal state on a fresh install, not an error worth a 500.
            log.warning("duckdb_query_failed", error=str(exc)[:200])
            return []

    async def query(self, sql: str, params: list[Any] | None = None) -> list[dict]:
        """Run a query without blocking the event loop.

        DuckDB's Python client is synchronous and CPU-bound while scanning.
        Running it inline would stall every other request this process is
        serving for the duration, so it goes to a worker thread.

        Args:
            sql: The query.
            params: Positional parameters.

        Returns:
            Rows as dicts.
        """
        return await asyncio.to_thread(self._query_sync, sql, params)

    async def revenue_by_day(self, days: int = 30) -> list[dict]:
        """Daily revenue and order counts from the marts.

        Args:
            days: Trailing window.

        Returns:
            One row per day, oldest first. Empty if the marts are not built.
        """
        return await self.query(
            """
            SELECT order_day       AS day,
                   orders          AS orders,
                   revenue_cents   AS revenue_cents,
                   avg_order_value_cents
              FROM mart_daily_revenue
             WHERE order_day >= CURRENT_DATE - CAST(? AS INTEGER)
             ORDER BY order_day
            """,
            [days],
        )

    async def top_products(self, limit: int = 10) -> list[dict]:
        """Best-selling products by revenue.

        Args:
            limit: How many to return.

        Returns:
            Products with units sold and revenue, or ``[]``.
        """
        return await self.query(
            """
            SELECT product_title,
                   sku,
                   units_sold,
                   revenue_cents
              FROM mart_product_performance
             ORDER BY revenue_cents DESC
             LIMIT ?
            """,
            [limit],
        )

    async def customer_summary(self) -> dict[str, Any]:
        """Aggregate customer metrics.

        Returns:
            A single summary row, or ``{}`` if the marts are not built.
        """
        rows = await self.query(
            """
            SELECT COUNT(*)                         AS customers,
                   SUM(orders)                      AS total_orders,
                   SUM(lifetime_value_cents)        AS lifetime_value_cents,
                   CAST(AVG(lifetime_value_cents) AS BIGINT) AS avg_lifetime_value_cents,
                   COUNT(*) FILTER (WHERE orders > 1) AS repeat_customers
              FROM mart_customer_value
            """
        )
        return rows[0] if rows else {}
