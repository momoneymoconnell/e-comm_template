"""One error shape for the entire system.

Every failure — a validation error in catalog, a declined card in payments, an
unhandled crash in orders — reaches the browser as the same JSON envelope:

```json
{
  "error": {
    "code": "not_found",
    "message": "Product not found.",
    "details": {"product_id": "0193..."},
    "request_id": "01JD2K..."
  }
}
```

Two rules make this safe as well as tidy:

* **`message` is written for the end user.** It is safe to render in the UI and
  never contains a stack trace, SQL, or an internal hostname.
* **`details` is a closed set.** Handlers populate it deliberately; exception
  text is never dumped into it. An unexpected exception always becomes a
  generic 500 with the real cause going to the logs only, because error strings
  are a classic information-disclosure channel (they leak table names, file
  paths and library versions to anyone probing the API).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ecom_shared.logging import get_logger, get_request_id

log = get_logger(__name__)

#: Starlette renamed this constant (422 "Unprocessable Entity" became
#: "Unprocessable Content" in the current HTTP spec) and deprecated the old
#: name. Using the literal keeps us off both spellings and immune to the churn.
HTTP_422_UNPROCESSABLE_CONTENT = 422


class AppError(Exception):
    """Base class for every *expected* failure.

    "Expected" means the service recognised the situation and chose to fail:
    a missing record, a duplicate email, an expired token. Raising these is
    normal control flow, so they are logged at WARNING, not ERROR.

    Anything that is *not* an `AppError` is a bug, and is handled by
    :func:`_unhandled_exception_handler` — 500, generic message, full traceback
    in the logs.

    Attributes:
        status_code: HTTP status to return.
        code: Stable machine-readable slug. Frontends switch on this, so treat
            it as part of the API contract and do not rename it casually.
        message: Human-readable, user-safe explanation.
        details: Optional structured context, e.g. which field was invalid.
    """

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "bad_request"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        """Create the error.

        Args:
            message: User-facing description. Must not leak internals.
            details: Optional structured context returned alongside the message.
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_response(self) -> JSONResponse:
        """Render this error as the standard JSON envelope."""
        return JSONResponse(
            status_code=self.status_code,
            content={
                "error": {
                    "code": self.code,
                    "message": self.message,
                    "details": self.details,
                    "request_id": get_request_id(),
                }
            },
        )


class ValidationFailedError(AppError):
    """422 — the request was well-formed but semantically invalid."""

    status_code = HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_failed"


class UnauthorizedError(AppError):
    """401 — no credentials, or credentials that are expired or forged."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class ForbiddenError(AppError):
    """403 — authenticated, but not allowed to do this.

    Deliberately distinct from 401: the client should *not* retry with fresh
    credentials, because the problem is permission, not identity.
    """

    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class NotFoundError(AppError):
    """404 — the resource does not exist, or the caller may not know it does.

    Returning 404 instead of 403 for records owned by someone else prevents
    attackers from enumerating IDs to learn what exists.
    """

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    """409 — the request collides with current state.

    Duplicate email on signup, checking out an empty cart, refunding twice.
    """

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class RateLimitedError(AppError):
    """429 — too many requests from this client."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class UpstreamError(AppError):
    """502 — a dependency we call (Stripe, another service) failed.

    Distinguishes "their fault" from "our fault", which matters when reading
    dashboards and when deciding whether a retry could help.
    """

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "upstream_error"


def _error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the standard envelope body."""
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": get_request_id(),
        }
    }


def register_exception_handlers(app: FastAPI, *, include_debug_detail: bool) -> None:
    """Install handlers so *every* exception becomes the standard envelope.

    Without this, FastAPI returns three different shapes — ``{"detail": ...}``
    for HTTPException, a nested list for validation errors, and bare HTML for
    unhandled crashes — and the frontend needs three code paths to parse them.

    Args:
        app: The application to attach handlers to.
        include_debug_detail: When ``True`` (development only), the real
            exception message is included in 500 responses to save you a trip to
            the logs. Always ``False`` in production.
    """

    @app.exception_handler(AppError)
    async def _app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        """Expected failures: log at WARNING, return the intended status."""
        log.warning("app_error", code=exc.code, message=exc.message, details=exc.details)
        return exc.to_response()

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """Reshape FastAPI's validation errors into a flat ``field -> message`` map.

        FastAPI's native output is a list of objects with a ``loc`` tuple, which
        is awkward to render next to a form input. We flatten it to
        ``{"email": "value is not a valid email address"}``.
        """
        fields: dict[str, str] = {}
        for err in exc.errors():
            # loc looks like ("body", "email"); drop the source segment.
            location = [str(part) for part in err["loc"][1:]] or [str(p) for p in err["loc"]]
            fields[".".join(location)] = err["msg"]
        return JSONResponse(
            status_code=HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_body("validation_failed", "Some fields need attention.", fields),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """Cover framework-raised 404s/405s so even those match the envelope."""
        code = {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            429: "rate_limited",
        }.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(code, str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Last line of defence: an unhandled exception is a bug.

        The traceback goes to the logs; the client gets a generic message plus
        the request ID, which is enough for you to find the exact log line
        without exposing anything about the internals.
        """
        log.error(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
            exc_info=exc,
        )
        details = {"exception": f"{type(exc).__name__}: {exc}"} if include_debug_detail else {}
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body(
                "internal_error",
                "Something went wrong on our end. Please try again.",
                details,
            ),
        )
