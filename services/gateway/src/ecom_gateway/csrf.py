"""Cross-site request forgery protection, using the double-submit pattern.

**The attack.** Browsers attach cookies to a request based on where it is
*going*, not where it came from. So a page on `evil.example` can submit a form
to `yourshop.example/api/orders/checkout`, and the browser helpfully includes
the victim's session cookie. Without a defence, the request succeeds.

**The two defences here, both required.**

1. ``SameSite=Lax`` on the session cookies (set by the auth service). The
   browser simply does not attach them to cross-site POSTs, which kills the
   classic form-submission attack outright. This is the strong layer.

2. Double-submit tokens, implemented below. The client must send a value in the
   ``X-CSRF-Token`` header that matches its ``csrf_token`` cookie. An attacker's
   page can cause the cookie to be *sent*, but cannot *read* it — the same-origin
   policy forbids that — so it cannot populate the header.

Why both, when the first is already effective: `SameSite` is enforced by the
browser, so it protects exactly as far as the browser's implementation goes,
and defaults have shifted more than once. The header check is enforced by us,
and costs a few lines.

**What is exempt, and why that is safe.** The Stripe webhook is excluded: it is
a server-to-server call with no cookies and no browser, and its authenticity is
established by HMAC signature verification instead — a far stronger check than
CSRF tokens. Safe methods are exempt because they must not change state anyway.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ecom_shared.logging import get_logger, get_request_id
from ecom_shared.security import constant_time_compare
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

log = get_logger(__name__)

#: Methods that must not change state, and so need no CSRF protection.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

#: Cookie holding the double-submit value. Readable by JavaScript on purpose —
#: the frontend has to copy it into the header. That is not a weakness: the
#: cookie is not a credential, it only proves the request came from a page able
#: to read same-origin cookies.
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"

#: Paths exempt from the check, matched as prefixes.
EXEMPT_PREFIXES = (
    # Server-to-server, authenticated by HMAC signature instead.
    "/api/payments/webhook",
    # Unauthenticated by design: sign-in and sign-up carry no session to forge,
    # and requiring a token before the client has one is a chicken-and-egg
    # problem for a first-time visitor.
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/password/forgot",
    "/api/auth/password/reset",
    "/api/auth/refresh",
    # Fire-and-forget beacons from the storefront. They write only pseudonymous
    # counters, so a forged one achieves nothing but a skewed statistic.
    "/api/analytics/events",
    "/health",
)

Dispatch = Callable[[Request], Awaitable[Response]]


class CsrfMiddleware(BaseHTTPMiddleware):
    """Require a matching CSRF token on cookie-authenticated state changes."""

    async def dispatch(self, request: Request, call_next: Dispatch) -> Response:
        """Verify the token, or pass the request through if exempt."""
        if request.method in SAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES):
            return await call_next(request)

        # A Bearer token is not sent automatically by the browser, so a
        # cross-site page cannot cause one to be attached. Requests
        # authenticated that way are not vulnerable to CSRF and are exempt.
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            return await call_next(request)

        cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
        header_token = request.headers.get(CSRF_HEADER_NAME)

        # No session cookie at all means there is nothing to forge, so an
        # anonymous POST is allowed through to be rejected on its own merits
        # (usually with a 401 that is much clearer than a CSRF error).
        if not cookie_token:
            return await call_next(request)

        if not header_token or not constant_time_compare(cookie_token, header_token):
            log.warning(
                "csrf_token_mismatch",
                path=path,
                method=request.method,
                has_header=bool(header_token),
            )
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "csrf_failed",
                        "message": "Your session could not be verified. Refresh and try again.",
                        "details": {},
                        "request_id": get_request_id(),
                    }
                },
            )

        return await call_next(request)
