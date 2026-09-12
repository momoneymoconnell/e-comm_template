"""Shared building blocks for the e-commerce microservices.

Every service in `services/` depends on this package. It deliberately contains
only *cross-cutting* concerns — things that must behave identically everywhere,
because inconsistency between services is where security holes and debugging
nightmares come from:

| Module        | Responsibility                                                |
|---------------|---------------------------------------------------------------|
| `config`      | Environment-variable settings, validated at startup           |
| `logging`     | Structured JSON logs with a request ID on every line          |
| `errors`      | One JSON error shape for every failure in the system          |
| `middleware`  | Request IDs, security headers, access logging                 |
| `db`          | Async SQLAlchemy engine + session, scoped to a service schema |
| `security`    | Password hashing and JWT signing/verification                 |
| `identity`    | Reading the caller's identity from gateway-signed headers     |
| `schemas`     | Base Pydantic models and pagination envelopes                 |
| `app`         | `create_service_app()` — wires all of the above together      |

Business logic never lives here. If a concept only matters to one service
(a product, an order, a refund), it belongs in that service.
"""

from ecom_shared.app import create_service_app
from ecom_shared.config import ServiceSettings
from ecom_shared.errors import (
    AppError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationFailedError,
)
from ecom_shared.identity import CallerIdentity, require_admin, require_user
from ecom_shared.logging import get_logger

__all__ = [
    "AppError",
    "CallerIdentity",
    "ConflictError",
    "ForbiddenError",
    "NotFoundError",
    "ServiceSettings",
    "UnauthorizedError",
    "ValidationFailedError",
    "create_service_app",
    "get_logger",
    "require_admin",
    "require_user",
]
