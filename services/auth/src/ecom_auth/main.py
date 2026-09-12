"""Auth service entrypoint.

Ten lines of wiring. Everything structural — logging, error handling, security
headers, health probes, the database lifecycle — comes from
`create_service_app()`, so this file only says what makes *this* service
different: its settings, its routers, and its first-boot hook.
"""

from __future__ import annotations

from ecom_shared.app import create_service_app

from ecom_auth.admin_router import router as admin_router
from ecom_auth.bootstrap import bootstrap_admin
from ecom_auth.config import AuthSettings
from ecom_auth.router import router as auth_router

settings = AuthSettings()

app = create_service_app(
    settings,
    routers=[auth_router, admin_router],
    title="Auth Service",
    description=(
        "Accounts, sessions and access control.\n\n"
        "Sessions use httpOnly cookies holding a short-lived access JWT and a "
        "rotating refresh token. Refresh-token reuse revokes every session for "
        "that user, so a stolen token buys an attacker one request at most."
    ),
    on_startup=[bootstrap_admin],
)
