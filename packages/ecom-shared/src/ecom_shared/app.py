"""`create_service_app()` — the one way a service becomes an HTTP server.

Every microservice's `main.py` is deliberately about ten lines long, because
all the wiring lives here. That is the point: logging, error shapes, security
headers, CORS, health probes and database lifecycle are identical everywhere,
so they are written once. A new service inherits all of it and only has to
describe what makes it different.

    # services/catalog/src/ecom_catalog/main.py
    settings = CatalogSettings()
    app = create_service_app(settings, routers=[products_router], base=Base)
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ecom_shared.config import ServiceSettings
from ecom_shared.db import Database
from ecom_shared.errors import register_exception_handlers
from ecom_shared.logging import configure_logging, get_logger
from ecom_shared.middleware import (
    AccessLogMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from ecom_shared.schemas import HealthStatus

log = get_logger(__name__)

#: Bumped on release. Surfaced by /health so you can confirm at a glance which
#: build is actually running in a container.
SERVICE_VERSION = "0.1.0"


def create_service_app(
    settings: ServiceSettings,
    *,
    routers: Sequence[APIRouter] = (),
    title: str | None = None,
    description: str = "",
    enable_database: bool = True,
    on_startup: Sequence[object] = (),
) -> FastAPI:
    """Build a fully configured FastAPI application.

    What this sets up, in order:

    1. Structured logging (before anything can log).
    2. A secrets sanity check that aborts startup in production if it fails.
    3. A lifespan that opens the database pool on boot and closes it on shutdown.
    4. Middleware — request ID, then access log, then security headers, then CORS.
    5. Exception handlers producing the shared error envelope.
    6. ``/health``, ``/health/live`` and ``/health/ready`` probes.
    7. The service's own routers.

    Args:
        settings: The service's settings instance.
        routers: Routers to mount. Each should carry its own prefix and tags.
        title: OpenAPI title. Defaults to a title-cased service name.
        description: OpenAPI description, shown at the top of ``/docs``.
        enable_database: Set ``False`` for a service with no Postgres schema
            (the gateway), which skips the pool and reports ``database: true``
            trivially.
        on_startup: Extra awaitable callables, each taking the app, run after
            the database is ready. Used for one-off tasks like the admin
            bootstrap in the auth service.

    Returns:
        The configured application, ready for uvicorn.
    """
    configure_logging(
        settings.service_name,
        settings.log_level,
        # Human-readable colour output locally; JSON everywhere else.
        json_output=settings.environment != "development",
    )
    settings.require_secrets()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Own the resources that must outlive a single request.

        Creating the connection pool per-request would open and close a TCP
        connection and run a TLS handshake on every call. Creating it here ties
        it to the process lifetime instead, and guarantees it is drained
        cleanly on shutdown so in-flight queries are not severed.
        """
        log.info(
            "service_starting",
            version=SERVICE_VERSION,
            environment=settings.environment,
            schema=settings.db_schema if enable_database else None,
        )

        if enable_database:
            app.state.db = Database(
                settings.database_url,
                schema=settings.db_schema,
                echo=settings.log_level == "DEBUG",
            )
        else:
            app.state.db = None

        for hook in on_startup:
            await hook(app)  # type: ignore[operator]

        log.info("service_ready")
        try:
            yield
        finally:
            if app.state.db is not None:
                await app.state.db.dispose()
            log.info("service_stopped")

    app = FastAPI(
        title=title or f"{settings.service_name.title()} Service",
        description=description,
        version=SERVICE_VERSION,
        lifespan=lifespan,
        # Interactive docs are a map of your entire attack surface. Useful in
        # development, switched off in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # Settings live on app.state so dependencies can reach them without a
    # module-level global, which would make the app impossible to test with a
    # different configuration.
    app.state.settings = settings

    # --- Middleware ---------------------------------------------------------
    # Starlette runs middleware in REVERSE order of registration, so the last
    # one added is outermost. Registering the request context last therefore
    # makes it the first to run — which is what we want, because everything
    # else logs, and every log line needs the request ID.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # Required for cookie auth: without it the browser drops the Set-Cookie
        # on cross-origin responses and sends no cookies on later requests.
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,  # cache the preflight for 10 minutes
    )
    app.add_middleware(SecurityHeadersMiddleware, is_production=settings.is_production)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app, include_debug_detail=not settings.is_production)

    # --- Health probes ------------------------------------------------------
    @app.get("/health", tags=["health"], summary="Full health report")
    async def health() -> HealthStatus:
        """Report service and dependency health.

        Returns ``status: "degraded"`` — not an error status — when the
        database is unreachable, so a monitor can distinguish "the process is
        wedged" from "the process is fine but its dependency is down".
        """
        db_ok = True
        if app.state.db is not None:
            db_ok = await app.state.db.check_health()
        return HealthStatus(
            status="ok" if db_ok else "degraded",
            service=settings.service_name,
            version=SERVICE_VERSION,
            database=db_ok,
            checked_at=datetime.now(UTC),
        )

    @app.get("/health/live", tags=["health"], summary="Liveness probe")
    async def liveness() -> dict[str, str]:
        """Answer as long as the event loop is running.

        Deliberately checks nothing else. If liveness depended on Postgres, a
        brief database blip would make the orchestrator kill and restart every
        healthy container at once — turning a small outage into a large one.
        """
        return {"status": "alive"}

    @app.get("/health/ready", tags=["health"], summary="Readiness probe")
    async def readiness() -> JSONResponse:
        """Report whether this instance should receive traffic.

        This one *does* check the database, and returns 503 when it is
        unreachable so the load balancer routes around this instance until it
        recovers — without restarting it.
        """
        db_ok = True if app.state.db is None else await app.state.db.check_health()
        return JSONResponse(
            status_code=200 if db_ok else 503,
            content={"status": "ready" if db_ok else "not_ready", "database": db_ok},
        )

    for router in routers:
        app.include_router(router)

    return app


def get_database(app: FastAPI) -> Database:
    """Fetch the live `Database` from an app, with a clear error if absent.

    Args:
        app: The running application.

    Returns:
        The service's database handle.

    Raises:
        RuntimeError: If the service was built with ``enable_database=False``.
    """
    db = getattr(app.state, "db", None)
    if db is None:
        raise RuntimeError(
            "This service was created with enable_database=False and has no database."
        )
    return db  # type: ignore[no-any-return]
