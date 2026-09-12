"""Structured logging with automatic request correlation.

Two properties matter when you are staring at logs from seven containers at
2am:

1. **Every line is JSON.** Grep still works, but so does
   ``docker compose logs | jq 'select(.status >= 500)'``.
2. **Every line carries the request ID.** One HTTP call touches the gateway,
   then orders, then payments. Filtering on a single ``request_id`` reconstructs
   that whole path in order.

The request ID is stored in a :class:`contextvars.ContextVar`, which is
per-task in asyncio. That means concurrent requests never see each other's IDs,
and you do not have to thread a logger argument through every function.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

# Set by RequestContextMiddleware at the start of each request, read by the
# processor below. Default "-" marks lines emitted outside any request
# (startup, shutdown, background jobs).
_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def set_request_id(request_id: str) -> None:
    """Bind ``request_id`` to the current asyncio task for subsequent logs."""
    _request_id.set(request_id)


def get_request_id() -> str:
    """Return the current request's ID, or ``"-"`` outside a request."""
    return _request_id.get()


def _inject_request_id(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Structlog processor that stamps the current request ID onto every event."""
    event_dict["request_id"] = _request_id.get()
    return event_dict


def configure_logging(service_name: str, level: str = "INFO", *, json_output: bool = True) -> None:
    """Initialise logging for the process. Call once, at startup.

    Also redirects the standard library's ``logging`` (used by uvicorn and
    SQLAlchemy) through structlog, so third-party output matches ours instead of
    interleaving two different formats.

    Args:
        service_name: Stamped on every line as ``service``, so logs from a
            combined stream stay attributable.
        level: Standard logging level name, e.g. ``"INFO"``.
        json_output: ``True`` emits one JSON object per line (production and
            Docker). ``False`` emits coloured, aligned, human-readable output —
            much nicer when running a single service in a terminal.
    """
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _inject_request_id,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, sqlalchemy, asyncpg) through the same pipeline.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn installs its own handlers; strip them so lines are not duplicated.
    for noisy in ("uvicorn", "uvicorn.error"):
        logging.getLogger(noisy).handlers = []
        logging.getLogger(noisy).propagate = True

    silence_uvicorn_access_log()

    # SQLAlchemy's INFO level echoes every statement. Useful when debugging,
    # overwhelming otherwise — so it only speaks up at DEBUG.
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if level == "DEBUG" else logging.WARNING
    )

    structlog.contextvars.bind_contextvars(service=service_name)


def silence_uvicorn_access_log() -> None:
    """Switch off uvicorn's own access logger.

    `AccessLogMiddleware` already emits one structured line per request, with
    timing and the request ID; uvicorn's logger prints a second, less useful
    line for the same request.

    This must be called **after** uvicorn has configured logging, not just
    during `configure_logging()`. uvicorn imports the application module first
    and applies its own `dictConfig` afterwards, so anything set at import time
    is simply overwritten. `create_service_app()` therefore also calls this
    from the lifespan startup, which runs late enough to stick — and does so
    regardless of whether whoever launched the process remembered
    `--no-access-log`.
    """
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.propagate = False
    access_logger.disabled = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structured logger.

    Usage mirrors the stdlib, but keyword arguments become JSON fields rather
    than being interpolated into a string::

        log = get_logger(__name__)
        log.info("order.paid", order_id=str(order.id), amount_cents=order.total_cents)

    Args:
        name: Logger name; pass ``__name__`` from the calling module.

    Returns:
        A bound logger that emits through the configured processor chain.
    """
    return structlog.get_logger(name)  # type: ignore[no-any-return]
