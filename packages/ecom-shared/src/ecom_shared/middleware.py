"""ASGI middleware applied to every service.

Three concerns, deliberately kept separate so each is easy to read and to test:

* :class:`RequestContextMiddleware` — assigns/propagates the request ID.
* :class:`AccessLogMiddleware` — one structured line per request, with timing.
* :class:`SecurityHeadersMiddleware` — browser hardening headers on responses.

Order matters. They are installed in `create_service_app()` so that the request
ID is set *before* anything else runs, guaranteeing that even a crash inside
another middleware is logged with a usable correlation ID.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ecom_shared.logging import get_logger, set_request_id

log = get_logger(__name__)

# Header used to carry the correlation ID between the browser, the gateway and
# each internal service. `X-Request-ID` is the de-facto standard and is
# understood by most reverse proxies and log aggregators.
REQUEST_ID_HEADER = "X-Request-ID"

Dispatch = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Give every request a stable ID and echo it back to the caller.

    If the incoming request already carries ``X-Request-ID`` (because the
    gateway generated one and is forwarding to a downstream service) we reuse
    it. Otherwise we mint a new UUID4. Either way the ID is bound to the
    context so `get_logger()` stamps it on every subsequent line, and it is
    echoed in the response header so the browser — and the user reporting a
    bug — can quote it back to you.
    """

    async def dispatch(self, request: Request, call_next: Dispatch) -> Response:
        """Bind the request ID for the lifetime of this request."""
        incoming = request.headers.get(REQUEST_ID_HEADER)
        # Untrusted input: cap the length and keep only safe characters so a
        # malicious client cannot inject newlines into our log stream.
        if incoming and len(incoming) <= 64 and incoming.replace("-", "").isalnum():
            request_id = incoming
        else:
            request_id = str(uuid.uuid4())

        set_request_id(request_id)
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Emit exactly one structured log line per request, including failures.

    Uses ``try/finally`` so a crashing handler still produces a line with its
    duration — otherwise the slowest, most interesting requests would be
    precisely the ones missing from your logs.
    """

    #: Paths that would otherwise flood the logs with no diagnostic value.
    #: Docker healthchecks hit /health every few seconds, forever.
    QUIET_PATHS = frozenset({"/health", "/health/live", "/health/ready", "/metrics"})

    async def dispatch(self, request: Request, call_next: Dispatch) -> Response:
        """Time the request and log method, path, status and duration."""
        if request.url.path in self.QUIET_PATHS:
            return await call_next(request)

        started = time.perf_counter()
        status_code = 500  # assume the worst; corrected on success
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            log.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                client_ip=client_ip(request),
            )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach defensive headers to every response.

    These are cheap, standard mitigations that real e-commerce sites are
    expected to have. Each one closes a specific attack:

    * ``X-Content-Type-Options: nosniff`` — stops a browser from "helpfully"
      re-interpreting a JSON response as HTML and executing script in it.
    * ``X-Frame-Options: DENY`` — blocks clickjacking, where an attacker
      iframes your checkout invisibly over their own page.
    * ``Referrer-Policy`` — prevents leaking full URLs (which may carry order
      IDs) to third-party sites in the ``Referer`` header.
    * ``Strict-Transport-Security`` — forces HTTPS for a year, defeating
      downgrade attacks. Only sent in production, since local dev is HTTP and
      an HSTS entry for ``localhost`` is painful to undo in a browser.
    * ``Content-Security-Policy`` — API responses are never a document, so the
      policy denies everything. The frontend sets its own, looser policy.
    """

    def __init__(self, app: object, *, is_production: bool) -> None:
        """Store the environment so HSTS is only sent where HTTPS exists.

        Args:
            app: The wrapped ASGI application.
            is_production: Whether to include ``Strict-Transport-Security``.
        """
        super().__init__(app)  # type: ignore[arg-type]
        self.is_production = is_production

    async def dispatch(self, request: Request, call_next: Dispatch) -> Response:
        """Apply the header set to the outgoing response."""
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=(), payment=(self)"
        )
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        if self.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


def client_ip(request: Request) -> str:
    """Best-effort client IP, honouring one layer of trusted proxy.

    Only the *first* entry of ``X-Forwarded-For`` is used, and only because the
    gateway is the sole thing allowed to reach these services on the compose
    network. Behind a public load balancer you must configure that LB to
    overwrite (not append to) the header, or clients can spoof their IP and
    defeat rate limiting.

    Returns:
        The client IP, or ``"unknown"`` if it cannot be determined.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
