"""Queueing and delivering email.

Split into two halves that never run at the same time:

* `queue_notification` — called from a request. Renders the template, writes a
  row, returns. No network I/O, so it is fast and cannot fail because a mail
  server is down.
* `deliver_pending` — called by the background worker. Claims messages, talks
  SMTP, records the outcome.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any, cast

import aiosmtplib
from ecom_shared.logging import get_logger
from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from ecom_notifications.config import NotificationSettings
from ecom_notifications.models import Notification
from ecom_notifications.templates_registry import render_template

log = get_logger(__name__)


async def queue_notification(
    session: AsyncSession,
    settings: NotificationSettings,
    *,
    template: str,
    to_email: str,
    context: dict[str, Any],
) -> Notification:
    """Render a template and queue the result for delivery.

    Rendering happens now, at queue time, rather than at send time. A template
    bug therefore surfaces as a 422 on the calling request — where someone will
    see it — instead of as a message that silently fails to send hours later.

    Args:
        session: Active session.
        settings: Supplies the brand name used in the layout.
        template: Template key.
        to_email: Recipient.
        context: Template variables.

    Returns:
        The queued notification.

    Raises:
        ValidationFailedError: If the template does not exist.
    """
    rendered = render_template(template, context, site_name=settings.email_from_name)
    notification = Notification(
        template=template,
        to_email=to_email.strip().lower(),
        subject=rendered.subject,
        body_html=rendered.html,
        body_text=rendered.text,
        context=context,
        status="queued",
    )
    session.add(notification)
    await session.flush()

    log.info("notification_queued", template=template, notification_id=str(notification.id))
    return notification


def _backoff_delay(attempts: int) -> timedelta:
    """Exponential backoff between delivery attempts.

    1 minute, then 2, 4, 8, 16 — capped at 30. A mail server having a bad
    minute should not be hammered by every queued message at once, and a
    genuinely broken address should not be retried tightly forever.

    Args:
        attempts: How many attempts have already been made.

    Returns:
        How long to wait before the next one.
    """
    return timedelta(minutes=min(2 ** max(0, attempts - 1), 30))


async def claim_pending(session: AsyncSession, *, limit: int = 20) -> list[Notification]:
    """Atomically claim a batch of due messages for this worker.

    Uses ``FOR UPDATE SKIP LOCKED``. The lock is what makes it safe to run more
    than one worker: each claims a different set of rows, and neither waits on
    the other. Without `SKIP LOCKED` a second worker would block on the first
    one's rows; without the lock entirely, both would claim the same messages
    and send every email twice.

    Args:
        session: Active session.
        limit: Maximum messages to claim in one batch.

    Returns:
        The claimed messages, now marked ``sending``.
    """
    now = datetime.now(UTC)
    result = await session.execute(
        select(Notification)
        .where(Notification.status == "queued", Notification.next_attempt_at <= now)
        .order_by(Notification.next_attempt_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    claimed = list(result.scalars().all())
    if not claimed:
        return []

    await session.execute(
        update(Notification)
        .where(Notification.id.in_([n.id for n in claimed]))
        .values(status="sending")
    )
    await session.flush()
    return claimed


async def send_email(settings: NotificationSettings, notification: Notification) -> None:
    """Deliver one message over SMTP.

    Builds a `multipart/alternative` message: the plain-text part first, then
    the HTML. Order matters — the standard says the *last* part is the most
    preferred, so text-first/HTML-second is what makes a graphical client show
    the HTML while a text-only one falls back cleanly.

    Args:
        settings: SMTP connection details.
        notification: The message to send.

    Raises:
        aiosmtplib.SMTPException: On any delivery failure. The caller records
            it and schedules a retry.
    """
    message = EmailMessage()
    message["From"] = f"{settings.email_from_name} <{settings.email_from}>"
    message["To"] = notification.to_email
    message["Subject"] = notification.subject
    message.set_content(notification.body_text)
    message.add_alternative(notification.body_html, subtype="html")

    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username or None,
        password=settings.smtp_password.get_secret_value() or None,
        start_tls=settings.smtp_use_tls,
        timeout=15,
    )


async def deliver_pending(session: AsyncSession, settings: NotificationSettings) -> tuple[int, int]:
    """Attempt delivery of every due message. One pass of the worker loop.

    Args:
        session: Active session.
        settings: SMTP details and the attempt limit.

    Returns:
        ``(sent, failed)`` counts for this pass.
    """
    claimed = await claim_pending(session)
    if not claimed:
        return 0, 0

    sent = failed = 0
    for notification in claimed:
        notification.attempts += 1
        try:
            await send_email(settings, notification)
        except Exception as exc:  # noqa: BLE001 - any failure is a retry candidate
            notification.last_error = str(exc)[:1000]
            if notification.attempts >= settings.max_attempts:
                notification.status = "failed"
                failed += 1
                log.error(
                    "notification_permanently_failed",
                    notification_id=str(notification.id),
                    template=notification.template,
                    attempts=notification.attempts,
                    error=str(exc)[:200],
                )
            else:
                notification.status = "queued"
                notification.next_attempt_at = datetime.now(UTC) + _backoff_delay(
                    notification.attempts
                )
                log.warning(
                    "notification_retry_scheduled",
                    notification_id=str(notification.id),
                    attempts=notification.attempts,
                    next_attempt=notification.next_attempt_at.isoformat(),
                )
        else:
            notification.status = "sent"
            notification.sent_at = datetime.now(UTC)
            notification.last_error = None
            sent += 1
            log.info(
                "notification_sent",
                notification_id=str(notification.id),
                template=notification.template,
            )

    await session.commit()
    return sent, failed


async def purge_old(session: AsyncSession, settings: NotificationSettings) -> int:
    """Delete messages older than the retention window.

    Transactional email has no reason to be kept indefinitely, and a growing
    table of everyone's email addresses and order contents is a liability, not
    an asset. Under GDPR's storage-limitation principle, keeping personal data
    longer than you need it is itself the problem.

    Args:
        session: Active session.
        settings: Supplies the retention window.

    Returns:
        How many rows were deleted.
    """
    cutoff = datetime.now(UTC) - timedelta(days=settings.retention_days)
    result = await session.execute(delete(Notification).where(Notification.created_at < cutoff))
    deleted = cast("CursorResult[Any]", result).rowcount or 0
    if deleted:
        log.info("notifications_purged", deleted=deleted, older_than_days=settings.retention_days)
    return deleted
