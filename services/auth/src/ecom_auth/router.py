"""HTTP routes for accounts and sessions.

Thin by design. Each handler translates HTTP into a call on `service.py`,
then translates the result back into a response — no business rules live here.
Reading this file should tell you the shape of the API; reading `service.py`
tells you what it actually does.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from ecom_shared.errors import UnauthorizedError
from ecom_shared.identity import (
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_NAME_INSECURE,
    CallerIdentity,
    CurrentUser,
)
from ecom_shared.logging import get_logger
from ecom_shared.middleware import client_ip
from ecom_shared.schemas import Message
from ecom_shared.security import generate_token
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ecom_auth import service
from ecom_auth.config import AuthSettings
from ecom_auth.cookies import clear_session_cookies, set_session_cookies
from ecom_auth.deps import get_db, get_settings
from ecom_auth.models import RefreshToken, User
from ecom_auth.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SessionResponse,
    SessionSummary,
    UpdateProfileRequest,
    UserResponse,
    VerifyEmailRequest,
)

log = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

Db = Annotated[AsyncSession, Depends(get_db)]
Settings = Annotated[AuthSettings, Depends(get_settings)]


def _client_context(request: Request) -> tuple[str | None, str | None]:
    """Extract ``(ip, user_agent)`` for audit and session records."""
    ip = client_ip(request)
    return (None if ip == "unknown" else ip), request.headers.get("User-Agent")


async def _send_verification_email(db: AsyncSession, settings: AuthSettings, user: User) -> None:
    """Issue a verification token and dispatch the email.

    Failures are logged and swallowed. A mail problem must not fail a
    registration that otherwise succeeded - the account exists, the customer is
    signed in, and they can request another link from their account page.

    Args:
        db: Active session.
        settings: Supplies the URL and token lifetime.
        user: Who to verify.
    """
    raw = await service.create_email_verification(db, settings, user=user)
    verify_url = f"{settings.public_web_url}/verify-email?token={raw}"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{settings.notifications_url}/notifications/send",
                json={
                    "template": "email_verification",
                    "to": user.email,
                    "context": {
                        "fullName": user.full_name or "there",
                        "verifyUrl": verify_url,
                        "expiresDays": settings.email_verification_ttl_seconds // 86_400,
                    },
                },
                headers={"Authorization": f"Bearer {_service_token(settings)}"},
            )
            # Checked, not assumed. A silently-dropped 401 here is how the
            # password reset email went unnoticed for as long as it did.
            response.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("verification_email_failed", user_id=str(user.id), error=str(exc))


def _service_token(settings: AuthSettings) -> str:
    """Mint a short-lived token for calling the notifications service."""
    from ecom_shared.security import create_service_token

    return create_service_token(
        service_name="auth",
        secret_key=settings.jwt_secret_key.get_secret_value(),
        ttl_seconds=60,
    )


def _read_refresh_cookie(request: Request) -> str | None:
    """Read the refresh token from whichever cookie name is in use."""
    return request.cookies.get(REFRESH_COOKIE_NAME) or request.cookies.get(
        REFRESH_COOKIE_NAME_INSECURE
    )


async def _establish_session(
    db: AsyncSession,
    settings: AuthSettings,
    response: Response,
    user: User,
    request: Request,
) -> SessionResponse:
    """Mint tokens, set cookies, and build the login/refresh response body.

    Shared by login, register and refresh so the three cannot diverge — a
    difference in cookie flags between "signed up" and "signed in" would be a
    silent, hard-to-spot security gap.

    Args:
        db: Active session.
        settings: Service settings.
        response: The outgoing response, mutated with cookies.
        user: The authenticated user.
        request: Used for IP and user agent.

    Returns:
        The session payload.
    """
    ip, user_agent = _client_context(request)
    access_token = service.build_access_token(user, settings)
    refresh_token, _ = await service.issue_refresh_token(
        db, settings, user=user, ip_address=ip, user_agent=user_agent
    )
    csrf_token = generate_token(32)

    set_session_cookies(
        response,
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
        access_ttl=settings.access_token_ttl_seconds,
        refresh_ttl=settings.refresh_token_ttl_seconds,
        secure=settings.cookie_secure,
        refresh_path=settings.refresh_cookie_path,
    )
    return SessionResponse(
        user=UserResponse.model_validate(user),
        expires_in=settings.access_token_ttl_seconds,
        csrf_token=csrf_token,
    )


# -----------------------------------------------------------------------------
# Registration and sign-in
# -----------------------------------------------------------------------------


@router.post(
    "/register",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account and sign in",
)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Db,
    settings: Settings,
) -> SessionResponse:
    """Register a new customer and start a session immediately.

    Signing in as part of registration avoids an awkward "your account was
    created, now log in" step that loses people mid-checkout.

    Note:
        A duplicate address returns 409, which does reveal that the address is
        registered. The strictly enumeration-proof alternative is to return
        success and email the existing owner instead, but it costs the user a
        confusing dead end at checkout. The sensitive path — password reset —
        *is* enumeration-proof; see `forgot_password`.
    """
    ip, user_agent = _client_context(request)
    user = await service.register_user(
        db,
        settings,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        ip_address=ip,
        user_agent=user_agent,
    )
    await _send_verification_email(db, settings, user)
    return await _establish_session(db, settings, response, user, request)


@router.post("/login", response_model=SessionResponse, summary="Sign in")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Db,
    settings: Settings,
) -> SessionResponse:
    """Verify credentials and issue session cookies."""
    ip, user_agent = _client_context(request)
    user = await service.authenticate(
        db,
        settings,
        email=payload.email,
        password=payload.password,
        ip_address=ip,
        user_agent=user_agent,
    )
    return await _establish_session(db, settings, response, user, request)


@router.post("/refresh", response_model=SessionResponse, summary="Renew the session")
async def refresh(
    request: Request,
    response: Response,
    db: Db,
    settings: Settings,
) -> SessionResponse:
    """Exchange the refresh cookie for a new token pair.

    Called by the frontend when an access token is close to expiry, and
    automatically after any 401. Rotation means the old token stops working the
    instant this succeeds.

    Raises:
        UnauthorizedError: If no refresh cookie is present, or it is invalid,
            expired, or has already been used.
    """
    raw_token = _read_refresh_cookie(request)
    if not raw_token:
        raise UnauthorizedError("No active session.")

    ip, user_agent = _client_context(request)
    user, new_refresh = await service.rotate_refresh_token(
        db, settings, raw_token=raw_token, ip_address=ip, user_agent=user_agent
    )

    access_token = service.build_access_token(user, settings)
    csrf_token = generate_token(32)
    set_session_cookies(
        response,
        access_token=access_token,
        refresh_token=new_refresh,
        csrf_token=csrf_token,
        access_ttl=settings.access_token_ttl_seconds,
        refresh_ttl=settings.refresh_token_ttl_seconds,
        secure=settings.cookie_secure,
        refresh_path=settings.refresh_cookie_path,
    )
    return SessionResponse(
        user=UserResponse.model_validate(user),
        expires_in=settings.access_token_ttl_seconds,
        csrf_token=csrf_token,
    )


@router.post("/logout", response_model=Message, summary="Sign out of this session")
async def logout(request: Request, response: Response, db: Db, settings: Settings) -> Message:
    """Revoke the current refresh token and clear cookies.

    Always reports success, even with no session — "log out" failing is
    confusing, and there is nothing to protect by reporting it.
    """
    if raw_token := _read_refresh_cookie(request):
        await service.revoke_refresh_token(db, raw_token)
    clear_session_cookies(
        response, secure=settings.cookie_secure, refresh_path=settings.refresh_cookie_path
    )
    return Message(message="Signed out.")


@router.post("/logout-all", response_model=Message, summary="Sign out everywhere")
async def logout_all(
    identity: CurrentUser, response: Response, db: Db, settings: Settings
) -> Message:
    """Revoke every session for the current user, on every device."""
    count = await service.revoke_all_sessions(db, identity.user_id, reason="user_logout_all")
    clear_session_cookies(
        response, secure=settings.cookie_secure, refresh_path=settings.refresh_cookie_path
    )
    return Message(message=f"Signed out of {count} session(s).")


# -----------------------------------------------------------------------------
# Profile
# -----------------------------------------------------------------------------


@router.get("/me", response_model=UserResponse, summary="Current user")
async def me(identity: CurrentUser, db: Db) -> UserResponse:
    """Return the signed-in user.

    Reads from the database rather than trusting the token's claims, so a role
    change or deactivation made in the last few minutes is reflected here even
    though the access token still carries the old values.
    """
    user = await service.get_user_by_id(db, identity.user_id)
    return UserResponse.model_validate(user)


@router.patch("/me", response_model=UserResponse, summary="Update your profile")
async def update_me(payload: UpdateProfileRequest, identity: CurrentUser, db: Db) -> UserResponse:
    """Update the current user's own profile fields."""
    user = await service.get_user_by_id(db, identity.user_id)
    if payload.full_name is not None:
        user.full_name = payload.full_name.strip() or None
    return UserResponse.model_validate(user)


@router.get("/sessions", response_model=list[SessionSummary], summary="Active sessions")
async def sessions(identity: CurrentUser, request: Request, db: Db) -> list[SessionSummary]:
    """List the current user's active sessions.

    Lets someone notice a session they do not recognise and end it. The session
    matching the current request is flagged so they do not revoke themselves by
    accident.
    """
    from ecom_shared.security import hash_token

    current_hash = None
    if raw := _read_refresh_cookie(request):
        current_hash = hash_token(raw)

    rows = await service.list_sessions(db, identity.user_id)
    return [
        SessionSummary(
            id=row.id,
            created_at=row.created_at,
            expires_at=row.expires_at,
            user_agent=row.user_agent,
            ip_address=row.ip_address,
            is_current=row.token_hash == current_hash,
        )
        for row in rows
    ]


@router.delete("/sessions/{session_id}", response_model=Message, summary="Revoke one session")
async def revoke_session(session_id: str, identity: CurrentUser, db: Db) -> Message:
    """Revoke a single session by ID.

    The ``user_id`` filter in the query is the authorisation check: a session
    belonging to someone else simply does not match, so it reports "not found"
    rather than revealing that the ID exists.
    """
    from datetime import UTC, datetime
    from uuid import UUID

    from ecom_shared.errors import NotFoundError

    try:
        target = UUID(session_id)
    except ValueError as exc:
        raise NotFoundError("Session not found.") from exc

    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.id == target,
            RefreshToken.user_id == identity.user_id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError("Session not found.")

    row.revoked_at = datetime.now(UTC)
    return Message(message="Session revoked.")


# -----------------------------------------------------------------------------
# Passwords
# -----------------------------------------------------------------------------


@router.post("/password/change", response_model=Message, summary="Change your password")
async def change_password(
    payload: ChangePasswordRequest,
    identity: CurrentUser,
    request: Request,
    response: Response,
    db: Db,
    settings: Settings,
) -> Message:
    """Change the signed-in user's password and end all sessions."""
    ip, user_agent = _client_context(request)
    user = await service.get_user_by_id(db, identity.user_id)
    await service.change_password(
        db,
        user=user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        ip_address=ip,
        user_agent=user_agent,
    )
    clear_session_cookies(
        response, secure=settings.cookie_secure, refresh_path=settings.refresh_cookie_path
    )
    return Message(message="Password changed. Please sign in again.")


@router.post("/password/forgot", response_model=Message, summary="Request a reset link")
async def forgot_password(payload: ForgotPasswordRequest, db: Db, settings: Settings) -> Message:
    """Email a password reset link, if the address has an active account.

    **The response is identical whether or not the account exists.** This is
    the whole point of the endpoint's design: an unauthenticated form that
    answers "yes, that email is registered" is a customer-list extraction tool,
    and it is the single most commonly overlooked enumeration vector.
    """
    generic = Message(message="If that email has an account, a reset link is on its way.")

    result = await service.create_password_reset(db, settings, email=payload.email)
    if result is None:
        return generic

    user, raw_token = result
    reset_url = f"{settings.public_web_url}/reset-password?token={raw_token}"

    # Dispatched to the notifications service. A failure to send must not fail
    # the request: it would make the response time differ between "account
    # exists" and "does not", reintroducing the enumeration oracle through the
    # back door.
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{settings.notifications_url}/notifications/send",
                json={
                    "template": "password_reset",
                    "to": user.email,
                    "context": {
                        "fullName": user.full_name or "there",
                        "resetUrl": reset_url,
                        "expiresMinutes": settings.password_reset_ttl_seconds // 60,
                    },
                },
                # The notifications send endpoint requires a service token.
                # Without this header it answers 401, and because the result
                # was never checked, every password reset email was silently
                # dropped while the endpoint reported success.
                headers={"Authorization": f"Bearer {_service_token(settings)}"},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("password_reset_email_failed", user_id=str(user.id), error=str(exc))

    return generic


@router.post("/email/verify", response_model=Message, summary="Confirm an email address")
async def verify_email(payload: VerifyEmailRequest, db: Db) -> Message:
    """Redeem an emailed verification link.

    Unauthenticated on purpose: the link is often opened on a different device
    from the one that signed up, and requiring a session there is a reliable
    way to make people give up.
    """
    await service.consume_email_verification(db, raw_token=payload.token)
    return Message(message="Email confirmed. Thank you.")


@router.post("/email/resend", response_model=Message, summary="Send a new confirmation link")
async def resend_verification(identity: CurrentUser, db: Db, settings: Settings) -> Message:
    """Send the signed-in customer another verification link.

    Always reports success, including when the address is already verified.
    Saying "that is already confirmed" tells anyone holding a stolen session
    something about the account for no benefit.
    """
    user = await service.get_user_by_id(db, identity.user_id)
    if user.email_verified_at is None:
        await _send_verification_email(db, settings, user)

    return Message(message="If confirmation is needed, a new link is on its way.")


@router.post("/password/reset", response_model=Message, summary="Redeem a reset link")
async def reset_password(payload: ResetPasswordRequest, request: Request, db: Db) -> Message:
    """Set a new password using a token from a reset email."""
    ip, user_agent = _client_context(request)
    await service.consume_password_reset(
        db,
        raw_token=payload.token,
        new_password=payload.new_password,
        ip_address=ip,
        user_agent=user_agent,
    )
    return Message(message="Password updated. You can sign in now.")


# Re-exported for the admin router, which needs the same dependency aliases.
__all__ = ["CallerIdentity", "Db", "Settings", "router"]
