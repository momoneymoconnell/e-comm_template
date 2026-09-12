"""Create the initial administrator on first boot.

A fresh deployment has no accounts, so nobody can sign in to create the first
admin — the classic bootstrap problem. This runs once at startup and resolves
it from configuration.

Two properties make it safe to leave enabled permanently:

* **Idempotent.** If the address already exists, nothing happens. It will not
  reset your password on every container restart.
* **Configuration-gated.** With no `BOOTSTRAP_ADMIN_EMAIL` set it does nothing
  at all, so it cannot create an account you did not ask for.
"""

from __future__ import annotations

from ecom_shared.logging import get_logger
from ecom_shared.security import hash_password
from fastapi import FastAPI

from ecom_auth import service
from ecom_auth.config import AuthSettings
from ecom_auth.models import User

log = get_logger(__name__)


async def bootstrap_admin(app: FastAPI) -> None:
    """Ensure the configured admin account exists.

    Called from the service lifespan after the database is ready.

    Args:
        app: The running application, carrying settings and the database.
    """
    settings: AuthSettings = app.state.settings
    email = (settings.bootstrap_admin_email or "").strip().lower()
    password = settings.bootstrap_admin_password

    if not email or password is None or not password.get_secret_value():
        log.info("bootstrap_admin_skipped", reason="not_configured")
        return

    # Belt and braces: the allowlist is the authority on who may be an admin,
    # and the bootstrap is not permitted to bypass it.
    if not settings.is_admin_email(email):
        log.error(
            "bootstrap_admin_not_allowlisted",
            hint="Add BOOTSTRAP_ADMIN_EMAIL to ADMIN_EMAILS in your .env",
        )
        return

    async with app.state.db.session_factory() as db:
        existing = await service.get_user_by_email(db, email)
        if existing is not None:
            # Promote an account that was registered normally before the
            # allowlist named it. Never touches the password.
            if existing.role != "admin":
                existing.role = "admin"
                service.record_audit(
                    db,
                    action="user.bootstrapped_admin",
                    actor_user_id=existing.id,
                    target_type="user",
                    target_id=str(existing.id),
                    metadata={"reason": "existing account promoted on boot"},
                )
                await db.commit()
                log.info("bootstrap_admin_promoted", user_id=str(existing.id))
            else:
                log.info("bootstrap_admin_exists", user_id=str(existing.id))
            return

        admin = User(
            email=email,
            password_hash=hash_password(password.get_secret_value()),
            full_name="Administrator",
            role="admin",
            is_active=True,
        )
        db.add(admin)
        await db.flush()
        service.record_audit(
            db,
            action="user.bootstrapped_admin",
            actor_user_id=admin.id,
            target_type="user",
            target_id=str(admin.id),
            metadata={"reason": "created on first boot"},
        )
        await db.commit()

        log.warning(
            "bootstrap_admin_created",
            user_id=str(admin.id),
            # The password is NOT logged. It is in your .env; that is the only
            # place it should be.
            hint="Sign in and change this password, then clear "
            "BOOTSTRAP_ADMIN_PASSWORD from your .env.",
        )
