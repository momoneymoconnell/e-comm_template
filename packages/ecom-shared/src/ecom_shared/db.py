"""Async database access, scoped to one schema per service.

Architecture note — why one Postgres instance with several schemas, rather
than one database per service:

    Textbook microservices give each service its own database so nobody can
    reach into anyone else's tables. That property is what actually matters,
    and a Postgres *schema* plus a per-service role enforces it just as well:
    the `orders` role simply cannot ``SELECT`` from ``auth.users`` — the grant
    does not exist. What you give up is independent failure and independent
    scaling of the storage layer, which is not a trade worth six containers,
    six connection pools and six backup jobs at this size.

    When one service genuinely outgrows the box, moving it out is a `pg_dump`
    of a single schema and a changed ``POSTGRES_HOST``. Nothing in the
    application code assumes co-location: services talk to each other over
    HTTP, never through shared tables.

See ``docker/postgres/010-init-schemas.sql`` for the roles and grants that make
the isolation real.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ecom_shared.logging import get_logger

log = get_logger(__name__)

# Deterministic names for indexes and constraints.
#
# Without this, SQLAlchemy lets Postgres auto-name things, and Alembic's
# autogenerate then cannot tell "this index was renamed" from "this index was
# dropped and a new one added" — producing migrations that drop and recreate
# constraints for no reason. Setting the convention once, up front, avoids a
# whole category of confusing diffs later.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def build_metadata(schema: str) -> MetaData:
    """Create a :class:`MetaData` bound to one service's schema.

    Args:
        schema: Postgres schema name, e.g. ``"orders"``.

    Returns:
        Metadata carrying the schema and the shared naming convention.
    """
    return MetaData(schema=schema, naming_convention=NAMING_CONVENTION)


def declarative_base_for(schema: str) -> type[DeclarativeBase]:
    """Build the ORM base class for a service's models.

    Every service defines its models against its own base, so tables are
    automatically created inside that service's schema::

        # services/orders/src/ecom_orders/models.py
        Base = declarative_base_for("orders")

        class Order(Base):
            __tablename__ = "orders"   # becomes orders.orders

    Args:
        schema: The schema this service owns.

    Returns:
        A new `DeclarativeBase` subclass whose metadata targets `schema`.
    """

    class Base(DeclarativeBase):
        """Declarative base scoped to a single service schema."""

        metadata = build_metadata(schema)

    return Base


class Database:
    """Owns the connection pool and hands out sessions.

    One instance per service process, created during startup and disposed
    during shutdown.

    Attributes:
        engine: The async engine and its connection pool.
        session_factory: Produces `AsyncSession` objects bound to `engine`.
    """

    def __init__(
        self,
        database_url: str,
        *,
        schema: str,
        echo: bool = False,
        pool_size: int = 5,
        max_overflow: int = 10,
    ) -> None:
        """Create the engine and session factory.

        No connection is opened yet — SQLAlchemy pools lazily, so constructing
        this object is cheap and cannot fail because Postgres is still booting.

        Args:
            database_url: Async DSN (``postgresql+asyncpg://...``).
            schema: The schema to pin as the connection ``search_path``, so
                unqualified table names resolve inside this service's own
                schema. ``public`` is appended for the shared extension types
                (citext, pgcrypto) but the service still has no grant on any
                other service's schema, so isolation is unaffected.
            echo: Log every SQL statement. Debugging only — it is extremely
                noisy and can print parameter values.
            pool_size: Connections kept open per process. Postgres defaults to
                100 total; with 6 services this leaves ample headroom.
            max_overflow: Extra connections allowed during a traffic spike,
                closed again once idle.
        """
        self.schema = schema
        self.engine: AsyncEngine = create_async_engine(
            database_url,
            echo=echo,
            pool_size=pool_size,
            max_overflow=max_overflow,
            # Recycle connections before common 5-minute idle timeouts on
            # cloud Postgres proxies, which otherwise surface as random
            # "server closed the connection unexpectedly" errors.
            pool_recycle=280,
            # Cheap liveness check on checkout; turns a stale-connection crash
            # into a transparent reconnect.
            pool_pre_ping=True,
            connect_args={
                "server_settings": {
                    "search_path": f"{schema}, public",
                    # Tags connections in pg_stat_activity, so `SELECT * FROM
                    # pg_stat_activity` tells you which service is running that
                    # slow query.
                    "application_name": f"ecom-{schema}",
                }
            },
        )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,  # keep attributes readable after commit
            autoflush=False,  # flush explicitly, so ordering is never a surprise
        )

    async def session(self) -> AsyncIterator[AsyncSession]:
        """FastAPI dependency yielding a session with transactional semantics.

        The handler runs inside one transaction: it commits if the handler
        returns normally, and rolls back if it raises. That means a request
        either fully happened or did not happen at all — you can never end up
        with an order row whose line items failed to insert.

        Usage::

            @router.post("/orders")
            async def create_order(db: Annotated[AsyncSession, Depends(get_db)]) -> ...:
                db.add(order)          # no explicit commit needed

        Yields:
            An `AsyncSession` valid for the duration of the request.
        """
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def check_health(self) -> bool:
        """Run ``SELECT 1`` to confirm the database is reachable.

        Used by the readiness probe: a service that cannot reach Postgres is
        alive but must not receive traffic.

        Returns:
            ``True`` if the query succeeded.
        """
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # noqa: BLE001 - health checks must never raise
            log.warning("database_health_check_failed", error=str(exc))
            return False

    async def dispose(self) -> None:
        """Close every pooled connection. Called on shutdown."""
        await self.engine.dispose()


def utcnow_sql() -> Any:
    """SQL expression for the current UTC timestamp.

    Prefer this over a Python-side default for ``created_at``: it makes the
    *database* the single clock. Application containers can drift, be in
    different timezones, or be restarted mid-request; Postgres cannot disagree
    with itself, so ordering by ``created_at`` stays meaningful.

    Returns:
        A SQLAlchemy text clause suitable for ``server_default``.
    """
    return text("timezone('utc', now())")
