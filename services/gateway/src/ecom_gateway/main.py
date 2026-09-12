"""API gateway entrypoint.

The one component the browser talks to. It routes, rate-limits and enforces
CSRF; it does not authenticate, because every service verifies the access token
itself.
"""

from __future__ import annotations

import asyncio
import contextlib

import httpx
from ecom_shared.app import create_service_app
from ecom_shared.logging import get_logger, get_request_id
from ecom_shared.middleware import client_ip
from fastapi import APIRouter, FastAPI, Request
from starlette.responses import JSONResponse, Response

from ecom_gateway import proxy
from ecom_gateway.config import GatewaySettings
from ecom_gateway.csrf import CsrfMiddleware
from ecom_gateway.rate_limit import RateLimiter

log = get_logger(__name__)

settings = GatewaySettings()

router = APIRouter()

#: How often to drop expired rate-limit counters.
PRUNE_INTERVAL_SECONDS = 300


@router.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def gateway(path: str, request: Request) -> Response:
    """Rate-limit, then forward the request to its service.

    Rate limiting happens here rather than in middleware so it applies only to
    proxied API traffic — health probes and the gateway's own endpoints are not
    counted, which stops a Docker healthcheck from consuming a client's budget.

    Args:
        path: The remainder of the path after ``/api/``.
        request: The incoming request.

    Returns:
        The service's streamed response, or 429 if the client is over its
        limit.
    """
    limiter: RateLimiter = request.app.state.rate_limiter
    key = client_ip(request)

    allowed, remaining, reset_in = limiter.check(key)
    if not allowed:
        log.warning("rate_limited", client_ip=key, path=request.url.path)
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "rate_limited",
                    "message": "Too many requests. Please slow down.",
                    "details": {"retryAfterSeconds": reset_in},
                    "request_id": get_request_id(),
                }
            },
            headers={
                # Standard headers so a well-behaved client can back off on its
                # own rather than retrying into the wall.
                "Retry-After": str(reset_in),
                "X-RateLimit-Limit": str(limiter.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_in),
            },
        )

    response = await proxy.forward(request, request.app.state.http, settings)
    response.headers["X-RateLimit-Limit"] = str(limiter.limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


async def _startup(app: FastAPI) -> None:
    """Create the shared HTTP client, the rate limiter and the prune loop.

    One `AsyncClient` is shared across all requests so connections to the
    backend services are pooled. Creating one per request would mean a fresh
    TCP handshake for every call the browser makes — the single most common
    performance mistake in a Python proxy.
    """
    app.state.http = httpx.AsyncClient(
        timeout=proxy.PROXY_TIMEOUT,
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        # The gateway must relay the backend's own status codes — a 404 from
        # catalog has to reach the browser as a 404, not as an exception.
        follow_redirects=False,
    )
    app.state.rate_limiter = RateLimiter(
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )

    async def _prune_loop() -> None:
        """Periodically drop stale rate-limit counters.

        Without this the counter dict grows by one entry per unique client IP
        and never shrinks — a slow leak that a scraper rotating addresses turns
        into a fast one.
        """
        while True:
            await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
            try:
                removed = app.state.rate_limiter.prune()
                if removed:
                    log.debug("rate_limit_counters_pruned", removed=removed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - housekeeping must not die
                log.error("rate_limit_prune_failed", error=str(exc))

    app.state.prune_task = asyncio.create_task(_prune_loop(), name="rate-limit-prune")
    log.info(
        "gateway_ready",
        routes=sorted(settings.routes),
        rate_limit=f"{settings.rate_limit_requests}/{settings.rate_limit_window_seconds}s",
    )


async def _shutdown(app: FastAPI) -> None:
    """Close the shared client and stop the prune loop.

    An unclosed client leaks sockets; an orphaned task keeps the event loop
    from exiting, which makes `docker stop` wait the full ten seconds before
    killing the container.
    """
    task = getattr(app.state, "prune_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    client = getattr(app.state, "http", None)
    if client is not None:
        await client.aclose()


app = create_service_app(
    settings,
    routers=[router],
    title="API Gateway",
    description=(
        "The single public entrypoint.\n\n"
        "Routes /api/<service>/... to one of six known services, rate-limits "
        "per client, and enforces CSRF on cookie-authenticated state changes. "
        "Paths containing /internal/ are refused outright, so inventory "
        "movement, payment-intent creation and marking orders paid are not "
        "reachable from a browser even if a service token leaked."
    ),
    # No database: the gateway owns no tables and opens no connection.
    enable_database=False,
    on_startup=[_startup],
    on_shutdown=[_shutdown],
)

app.add_middleware(CsrfMiddleware)
