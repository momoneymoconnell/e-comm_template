"""Test fixtures for the auth service.

Tests run against a **real Postgres**, not SQLite or a mock. The auth schema
leans on Postgres-specific features — CITEXT for case-insensitive emails, INET
for addresses, JSONB for audit metadata, partial-index semantics — and a test
suite that swaps those out is testing a system you do not ship.

Isolation comes from transactions. Each test runs inside one that is rolled
back at the end, so tests cannot see each other's rows and the database is
unchanged afterwards. That is far faster than recreating the schema per test
and gives the same guarantee.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from ecom_auth.config import AuthSettings
from ecom_auth.deps import get_db
from ecom_auth.models import Base
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

#: Where the test database lives. Defaults match `make up` on a dev machine;
#: CI overrides them with its own service container.
TEST_DB = {
    "host": os.getenv("TEST_POSTGRES_HOST", "localhost"),
    "port": os.getenv("TEST_POSTGRES_PORT", "5433"),
    "db": os.getenv("TEST_POSTGRES_DB", "ecom"),
    "user": os.getenv("TEST_POSTGRES_USER", "svc_auth"),
    "password": os.getenv("SVC_AUTH_DB_PASSWORD", ""),
}


def _database_url() -> str:
    """Build the async DSN for the test database."""
    return (
        f"postgresql+asyncpg://{TEST_DB['user']}:{TEST_DB['password']}"
        f"@{TEST_DB['host']}:{TEST_DB['port']}/{TEST_DB['db']}"
    )


@pytest.fixture
def settings() -> AuthSettings:
    """Settings for the service under test.

    Uses a throwaway signing key and a deliberately tiny lockout threshold so
    the brute-force test does not need fifty requests to prove a point.
    """
    return AuthSettings(
        service_name="auth",
        db_schema="auth",
        environment="development",
        jwt_secret_key="test-secret-key-at-least-32-characters-long",
        postgres_password=TEST_DB["password"],
        postgres_host=TEST_DB["host"],
        postgres_port=int(TEST_DB["port"]),
        postgres_user=TEST_DB["user"],
        postgres_db=TEST_DB["db"],
        admin_emails="admin@example.com",
        max_login_attempts=3,
        cookie_secure=False,
        # Tests talk to the service directly rather than through the gateway,
        # so the cookie path is the service's own prefix. In a deployment this
        # is "/api/auth", the path the browser actually requests.
        refresh_cookie_path="/auth",
    )


@pytest_asyncio.fixture
async def engine():
    """A fresh engine per test. Skips the test if Postgres is unreachable.

    Function-scoped deliberately. pytest-asyncio gives each test its own event
    loop, and an asyncpg connection is bound to the loop that created it — a
    session-scoped engine hands the second test a connection belonging to a
    closed loop, which surfaces as the deeply unhelpful
    ``InternalClientError: got result for unknown protocol state``.

    `NullPool` keeps that honest: no connection is retained between tests, so
    none can leak across loops.
    """
    eng = create_async_engine(_database_url(), poolclass=NullPool)
    try:
        async with eng.connect():
            pass
    except Exception as exc:  # noqa: BLE001
        await eng.dispose()
        pytest.skip(f"Postgres not reachable for integration tests: {exc}")
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine) -> AsyncIterator[AsyncSession]:
    """Yield a session whose work is discarded when the test ends.

    The outer transaction is never committed. Application code inside may call
    `commit()` — `_persist_security_record` does — and that only commits the
    inner savepoint, leaving the outer transaction free to roll everything back.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = async_sessionmaker(bind=connection, expire_on_commit=False)()
        # `join_transaction_mode="create_savepoint"` is what makes an inner
        # commit() safe: it maps to RELEASE SAVEPOINT rather than COMMIT, so the
        # outer rollback below still undoes everything.
        session.sync_session.join_transaction_mode = "create_savepoint"
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest_asyncio.fixture
async def client(db, settings) -> AsyncIterator[AsyncClient]:
    """An HTTP client wired to the app, with the database swapped for `db`.

    The dependency override is why the app under test writes into the
    rolled-back transaction instead of the real schema.
    """
    from ecom_auth.admin_router import router as admin_router
    from ecom_auth.router import router as auth_router
    from ecom_shared.app import create_service_app

    app = create_service_app(settings, routers=[auth_router, admin_router], enable_database=False)
    app.dependency_overrides[get_db] = lambda: db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def base_metadata():
    """The auth schema metadata, for tests that inspect table definitions."""
    return Base.metadata
