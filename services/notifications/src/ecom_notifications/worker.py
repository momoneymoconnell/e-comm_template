"""The background delivery worker.

Runs as an asyncio task alongside the HTTP server rather than as a separate
container. At this scale that is the right trade: one fewer image to build and
deploy, and the work is I/O-bound, so it shares the event loop happily with
request handling.

It stops being the right trade when mail volume grows enough that delivery
competes with serving requests — at which point this module is already a
standalone loop and becomes its own container by changing the entrypoint,
nothing more.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

from ecom_shared.logging import get_logger
from fastapi import FastAPI

from ecom_notifications import service
from ecom_notifications.config import NotificationSettings

log = get_logger(__name__)

#: How often to purge expired messages. Hourly is ample for a retention window
#: measured in days, and keeps the delete small enough to be unnoticeable.
PURGE_INTERVAL_SECONDS = 3600


async def _worker_loop(app: FastAPI) -> None:
    """Poll the outbox forever, delivering what is due.

    Every iteration is wrapped in its own try/except. An unhandled exception
    would end the task silently — the service would look perfectly healthy
    while quietly never sending another email again, which is exactly the kind
    of failure nobody notices until a customer complains.

    Args:
        app: The running application, carrying settings and the database.
    """
    settings: NotificationSettings = app.state.settings
    last_purge = datetime.now(UTC)

    log.info("outbox_worker_started", poll_seconds=settings.outbox_poll_seconds)

    while True:
        try:
            async with app.state.db.session_factory() as session:
                sent, failed = await service.deliver_pending(session, settings)
                if sent or failed:
                    log.info("outbox_pass_complete", sent=sent, failed=failed)

                now = datetime.now(UTC)
                if (now - last_purge).total_seconds() >= PURGE_INTERVAL_SECONDS:
                    await service.purge_old(session, settings)
                    await session.commit()
                    last_purge = now

        except asyncio.CancelledError:
            # Shutdown. Re-raise so the task actually ends rather than looping.
            log.info("outbox_worker_stopping")
            raise
        except Exception as exc:  # noqa: BLE001 - the loop must survive anything
            log.error("outbox_worker_error", error=str(exc), exc_info=exc)

        await asyncio.sleep(settings.outbox_poll_seconds)


async def start_worker(app: FastAPI) -> None:
    """Start the outbox worker as a background task.

    Registered as a startup hook. The task handle is kept on `app.state` so
    shutdown can cancel it — an orphaned task would keep a database connection
    open and prevent the process from exiting cleanly.

    Args:
        app: The running application.
    """
    app.state.outbox_task = asyncio.create_task(_worker_loop(app), name="outbox-worker")


async def stop_worker(app: FastAPI) -> None:
    """Cancel the outbox worker and wait for it to finish.

    Registered as a shutdown hook. Awaiting the cancelled task matters: without
    it the process can exit while the worker is mid-send, which at best drops a
    log line and at worst leaves a message stuck in `sending` with no worker
    coming back for it.

    Args:
        app: The application being shut down.
    """
    task = getattr(app.state, "outbox_task", None)
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
