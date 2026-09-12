"""Notifications service entrypoint."""

from __future__ import annotations

from ecom_shared.app import create_service_app

from ecom_notifications.config import NotificationSettings
from ecom_notifications.router import admin_router, router
from ecom_notifications.worker import start_worker, stop_worker

settings = NotificationSettings()

app = create_service_app(
    settings,
    routers=[router, admin_router],
    title="Notifications Service",
    description=(
        "Transactional email via a durable outbox.\n\n"
        "A send request writes a row and returns immediately; a background "
        "worker does the SMTP conversation with exponential-backoff retries. "
        "A mail outage therefore delays receipts rather than failing the "
        "checkout that triggered them."
    ),
    on_startup=[start_worker],
    on_shutdown=[stop_worker],
)
