"""Business logic for accounts and sessions.

Kept separate from `router.py` on purpose: routers deal in HTTP (status codes,
cookies, headers), this module deals in rules (can this person sign in, is this
token still valid). The split means the rules are testable without spinning up
a web server, and the HTTP layer stays thin enough to audit at a glance.

Every function here takes an `AsyncSession` and does not commit — the request's
transaction is committed once, by the dependency in `ecom_shared.db`. So a
login that succeeds in issuing a token but fails while writing the audit row
rolls back entirely rather than leaving a session nobody can account for.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from ecom_shared.errors import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from ecom_shared.logging import get_logger
from ecom_shared.security import (
    create_jwt,
    generate_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    verify_password,
)
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ecom_auth.config import AuthSettings
from ecom_auth.models import (
    AuditEvent,
    EmailVerificationToken,
    LoginAttempt,
    PasswordResetToken,
    RefreshToken,
    User,
)

log = get_logger(__name__)

#: Argon2 hash of a throwaway value, used to equalise timing on the
#: "no such user" path. See `authenticate()`.
_DUMMY_HASH = hash_password("timing-equalisation-placeholder-not-a-real-password")


# -----------------------------------------------------------------------------
# Audit
# -----------------------------------------------------------------------------


def record_audit(
    session: AsyncSession,
    *,
    action: str,
    actor_user_id: UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Queue an audit row inside the caller's transaction.

    Deliberately part of the same transaction as the action it describes. If
    the action rolls back, so does its audit entry — an audit log that records
    things which did not happen is worse than none at all.

    Args:
        session: The active session.
        action: Dotted verb, e.g. ``"user.login"``.
        actor_user_id: Who performed it, if known.
        target_type: Kind of object acted on.
        target_id: Its identifier.
        ip_address: Source IP.
        user_agent: Client user agent.
        metadata: Extra structured context. Never put credentials here.
    """
    session.add(
        AuditEvent(
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip_address=ip_address,
            user_agent=_truncate(user_agent, 400),
            event_metadata=metadata or {},
        )
    )


async def _persist_security_record(session: AsyncSession) -> None:
    """Commit pending security writes that must survive a rejected request.

    Normally a request is one transaction: the handler returns and it commits,
    or the handler raises and everything rolls back. That is exactly right for
    business data — a failed checkout must not leave a half-written order.

    It is exactly wrong for security bookkeeping. Two cases in this service
    record something and *then* reject the request:

    * a failed login writes a `LoginAttempt`, then raises 401;
    * detected refresh-token reuse revokes every session, then raises 401.

    Under the default behaviour the raise rolls both back. The failed attempt
    is never recorded, so the lockout counter stays at zero and brute-force
    protection does nothing at all; the revocation is undone, so a stolen
    refresh token keeps working. Both defences would appear to be implemented
    and neither would function.

    Committing here makes those writes durable before the exception unwinds.
    The request dependency's subsequent rollback then finds an empty
    transaction and is a no-op.

    Args:
        session: The active session, with the security write already added.
    """
    await session.commit()


def _truncate(value: str | None, limit: int) -> str | None:
    """Clip a client-supplied string to `limit` characters.

    User-Agent is attacker-controlled and unbounded. Truncating before it
    reaches the database turns a potential error (or a row-size problem) into a
    harmless shortened string.
    """
    if value is None:
        return None
    return value[:limit]


# -----------------------------------------------------------------------------
# Lookups
# -----------------------------------------------------------------------------


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Fetch a user by email. Case-insensitive via the CITEXT column.

    Args:
        session: Active session.
        email: The address to look up.

    Returns:
        The user, or ``None``.
    """
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> User:
    """Fetch a user by primary key.

    Args:
        session: Active session.
        user_id: The user's ID.

    Returns:
        The user.

    Raises:
        NotFoundError: If no such user exists.
    """
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")
    return user


# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------


async def register_user(
    session: AsyncSession,
    settings: AuthSettings,
    *,
    email: str,
    password: str,
    full_name: str | None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> User:
    """Create a customer account.

    The role is always ``customer``, regardless of the allowlist. Being on
    ``ADMIN_EMAILS`` grants eligibility, not the role itself — the bootstrap
    routine or an existing admin must still make the promotion, so a stray
    entry in an env file cannot silently create a privileged account.

    Args:
        session: Active session.
        settings: Service settings.
        email: New account's address.
        password: Plaintext password, already length- and strength-validated.
        full_name: Optional display name.
        ip_address: Source IP, recorded in the audit log.
        user_agent: Client user agent, recorded in the audit log.

    Returns:
        The created user.

    Raises:
        ConflictError: If the address is already registered.
    """
    normalised = email.strip().lower()

    user = User(
        email=normalised,
        password_hash=hash_password(password),
        full_name=full_name.strip() if full_name else None,
        role="customer",
        is_active=True,
    )
    session.add(user)

    try:
        # Flush rather than commit: this sends the INSERT so a duplicate email
        # surfaces now, while still leaving the whole request in one
        # transaction that can roll back.
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        # Relies on the unique index rather than a prior SELECT. A
        # check-then-insert would let two simultaneous signups for the same
        # address both pass the check; the database constraint cannot be raced.
        raise ConflictError(
            "An account with this email already exists. Try signing in instead."
        ) from exc

    record_audit(
        session,
        action="user.registered",
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    log.info("user_registered", user_id=str(user.id))
    return user


# -----------------------------------------------------------------------------
# Authentication
# -----------------------------------------------------------------------------


async def count_recent_failures(session: AsyncSession, settings: AuthSettings, email: str) -> int:
    """Count failed sign-ins for `email` inside the lockout window.

    Args:
        session: Active session.
        settings: Provides the window length.
        email: The address being attempted.

    Returns:
        Number of failures in the window.
    """
    since = datetime.now(UTC) - timedelta(seconds=settings.login_attempt_window_seconds)
    result = await session.execute(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.email == email,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.created_at >= since,
        )
    )
    return int(result.scalar_one())


async def authenticate(
    session: AsyncSession,
    settings: AuthSettings,
    *,
    email: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> User:
    """Verify credentials and return the user.

    Defences applied here, and what each one stops:

    * **Lockout.** More than `max_login_attempts` failures for one address
      inside the window rejects further attempts, which makes online brute
      force impractical. Keyed on email, not IP, because an attacker with a
      botnet has unlimited IPs but only one target address.
    * **Constant-ish timing.** When the user does not exist we still run a full
      Argon2 verification against a dummy hash. Without it, "no such user"
      returns in microseconds while a real user takes ~50ms, and that gap is a
      reliable oracle for enumerating which email addresses have accounts.
    * **Uniform error message.** Wrong password, unknown user and disabled
      account all return the same text, for the same reason.
    * **Opportunistic rehash.** This is the one moment the plaintext is
      legitimately available, so a hash made with older parameters is upgraded
      transparently.

    Args:
        session: Active session.
        settings: Service settings.
        email: Submitted address.
        password: Submitted password.
        ip_address: Source IP, recorded on the attempt.
        user_agent: Client user agent, recorded on the attempt.

    Returns:
        The authenticated user.

    Raises:
        UnauthorizedError: For any failure, with a deliberately generic message.
    """
    normalised = email.strip().lower()
    generic_failure = "Incorrect email or password."

    def _record(succeeded: bool, reason: str | None) -> None:
        session.add(
            LoginAttempt(
                email=normalised,
                ip_address=ip_address,
                user_agent=_truncate(user_agent, 400),
                succeeded=succeeded,
                failure_reason=reason,
            )
        )

    failures = await count_recent_failures(session, settings, normalised)
    if failures >= settings.max_login_attempts:
        _record(False, "locked_out")
        await _persist_security_record(session)
        log.warning("login_locked_out", email_hash=hash_token(normalised)[:16], failures=failures)
        minutes = settings.login_attempt_window_seconds // 60
        raise UnauthorizedError(
            f"Too many failed attempts. Try again in {minutes} minutes, or reset your password."
        )

    user = await get_user_by_email(session, normalised)

    # Hash comparison runs either way. `asyncio.to_thread` keeps the ~50ms of
    # CPU-bound Argon2 work off the event loop, so a burst of logins cannot
    # stall every other request the process is serving.
    if user is None:
        await asyncio.to_thread(verify_password, password, _DUMMY_HASH)
        _record(False, "no_such_user")
        await _persist_security_record(session)
        raise UnauthorizedError(generic_failure)

    password_ok = await asyncio.to_thread(verify_password, password, user.password_hash)
    if not password_ok:
        _record(False, "bad_password")
        await _persist_security_record(session)
        raise UnauthorizedError(generic_failure)

    if not user.is_active:
        _record(False, "inactive")
        await _persist_security_record(session)
        # Same message as a bad password: confirming that a *disabled* account
        # exists still confirms the address is registered.
        raise UnauthorizedError(generic_failure)

    if settings.require_verified_email and user.email_verified_at is None:
        _record(False, "unverified_email")
        await _persist_security_record(session)
        # A specific message here, unlike the failures above. The password was
        # correct, so this person has already proved the account is theirs -
        # telling them what to do next reveals nothing they do not know, and a
        # generic "incorrect email or password" would be actively misleading.
        raise UnauthorizedError(
            "Confirm your email address before signing in. Check your inbox for the link."
        )

    if password_needs_rehash(user.password_hash):
        user.password_hash = await asyncio.to_thread(hash_password, password)
        log.info("password_rehashed", user_id=str(user.id))

    user.last_login_at = datetime.now(UTC)
    _record(True, None)
    record_audit(
        session,
        action="user.login",
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return user


# -----------------------------------------------------------------------------
# Sessions
# -----------------------------------------------------------------------------


def build_access_token(user: User, settings: AuthSettings) -> str:
    """Mint a short-lived access JWT for `user`.

    Args:
        user: The authenticated user.
        settings: Supplies the signing key and TTL.

    Returns:
        The encoded JWT.
    """
    return create_jwt(
        subject=str(user.id),
        token_type="access",  # noqa: S106 - a token *type* discriminator, not a secret
        secret_key=settings.jwt_secret_key.get_secret_value(),
        ttl_seconds=settings.access_token_ttl_seconds,
        algorithm=settings.jwt_algorithm,
        # Embedding the role lets every other service authorise without a
        # round-trip to auth on each request. The trade-off is up to 15 minutes
        # of staleness after a demotion, bounded by the access token TTL — and
        # `revoke_all_sessions()` closes that window immediately when it matters.
        extra_claims={"role": user.role, "email": user.email},
    )


async def issue_refresh_token(
    session: AsyncSession,
    settings: AuthSettings,
    *,
    user: User,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, RefreshToken]:
    """Create and persist a new refresh token.

    Args:
        session: Active session.
        settings: Supplies the TTL.
        user: Its owner.
        ip_address: Source IP, stored for the session list.
        user_agent: Client user agent, stored for the session list.

    Returns:
        A tuple of ``(plaintext token, stored row)``. The plaintext is the only
        copy that will ever exist — the row holds a hash — so it must go
        straight into the response cookie and nowhere else.
    """
    raw = generate_token(32)
    row = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds),
        user_agent=_truncate(user_agent, 400),
        ip_address=ip_address,
    )
    session.add(row)
    await session.flush()
    return raw, row


async def rotate_refresh_token(
    session: AsyncSession,
    settings: AuthSettings,
    *,
    raw_token: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[User, str]:
    """Exchange a refresh token for a fresh one, detecting reuse.

    The sequence, and why it is in this order:

    1. Look the token up by hash. Unknown → reject.
    2. If it is already revoked, this is a **reuse**. Either the client
       replayed a request or someone is using a stolen copy; since we cannot
       distinguish them, every session for that user is revoked. The legitimate
       user signs in again; the attacker's stolen token becomes worthless.
    3. If expired, reject without revoking anything — expiry is normal.
    4. Otherwise revoke this token, issue a replacement, and link them so the
       chain stays traceable.

    Args:
        session: Active session.
        settings: Service settings.
        raw_token: The plaintext token from the cookie.
        ip_address: Source IP.
        user_agent: Client user agent.

    Returns:
        ``(user, new plaintext refresh token)``.

    Raises:
        UnauthorizedError: If the token is unknown, reused, expired, or its
            owner has since been deactivated.
    """
    token_hash = hash_token(raw_token)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored = result.scalar_one_or_none()

    if stored is None:
        raise UnauthorizedError("Your session has expired. Please sign in again.")

    if stored.revoked_at is not None:
        log.warning(
            "refresh_token_reuse_detected",
            user_id=str(stored.user_id),
            token_id=str(stored.id),
        )
        await revoke_all_sessions(session, stored.user_id, reason="token_reuse_detected")
        record_audit(
            session,
            action="session.reuse_detected",
            actor_user_id=stored.user_id,
            target_type="user",
            target_id=str(stored.user_id),
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"revoked_token_id": str(stored.id)},
        )
        # Must outlive the 401 below, or the revocation is rolled back and the
        # stolen token keeps working.
        await _persist_security_record(session)
        raise UnauthorizedError("Your session is no longer valid. Please sign in again.")

    if stored.expires_at <= datetime.now(UTC):
        raise UnauthorizedError("Your session has expired. Please sign in again.")

    user = await session.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("Your session is no longer valid. Please sign in again.")

    stored.revoked_at = datetime.now(UTC)
    new_raw, new_row = await issue_refresh_token(
        session, settings, user=user, ip_address=ip_address, user_agent=user_agent
    )
    stored.rotated_to_id = new_row.id

    return user, new_raw


async def revoke_refresh_token(session: AsyncSession, raw_token: str) -> None:
    """Revoke one token, for a normal sign-out.

    Silently does nothing if the token is unknown or already revoked: logging
    out should always appear to succeed, and reporting "that token was not
    valid" would leak information for no benefit.

    Args:
        session: Active session.
        raw_token: The plaintext token from the cookie.
    """
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == hash_token(raw_token),
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )


async def revoke_all_sessions(
    session: AsyncSession, user_id: UUID, *, reason: str = "manual"
) -> int:
    """Revoke every active session for a user.

    The panic button. Triggered by refresh-token reuse, by a password change,
    by a role change, and by an admin deactivating the account.

    Args:
        session: Active session.
        user_id: Whose sessions to revoke.
        reason: Recorded in the log line for later investigation.

    Returns:
        How many sessions were revoked.
    """
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    count = cast("CursorResult[Any]", result).rowcount or 0
    log.info("sessions_revoked", user_id=str(user_id), count=count, reason=reason)
    return count


async def list_sessions(session: AsyncSession, user_id: UUID) -> list[RefreshToken]:
    """List a user's active sessions, newest first.

    Args:
        session: Active session.
        user_id: Whose sessions to list.

    Returns:
        Unrevoked, unexpired refresh tokens.
    """
    result = await session.execute(
        select(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > datetime.now(UTC),
        )
        .order_by(RefreshToken.created_at.desc())
    )
    return list(result.scalars().all())


# -----------------------------------------------------------------------------
# Passwords
# -----------------------------------------------------------------------------


async def change_password(
    session: AsyncSession,
    *,
    user: User,
    current_password: str,
    new_password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Change a signed-in user's password.

    Re-verifying the current password is what stops an attacker who has
    borrowed an unlocked laptop — or stolen a session via XSS — from locking
    the real owner out of their own account.

    All other sessions are revoked afterwards, because "change my password" is
    what a user does when they suspect compromise, and it would be useless if
    the attacker's session survived it.

    Args:
        session: Active session.
        user: The signed-in user.
        current_password: Must match the stored hash.
        new_password: The replacement, already strength-validated.
        ip_address: Source IP for the audit entry.
        user_agent: Client user agent for the audit entry.

    Raises:
        UnauthorizedError: If `current_password` is wrong.
        ConflictError: If the new password is the same as the old one.
    """
    if not await asyncio.to_thread(verify_password, current_password, user.password_hash):
        raise UnauthorizedError("Your current password is incorrect.")

    if await asyncio.to_thread(verify_password, new_password, user.password_hash):
        raise ConflictError("Your new password must be different from your current one.")

    user.password_hash = await asyncio.to_thread(hash_password, new_password)
    await revoke_all_sessions(session, user.id, reason="password_changed")
    record_audit(
        session,
        action="user.password_changed",
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        ip_address=ip_address,
        user_agent=user_agent,
    )


async def create_password_reset(
    session: AsyncSession, settings: AuthSettings, *, email: str
) -> tuple[User, str] | None:
    """Issue a password reset token, if the address has an account.

    Args:
        session: Active session.
        settings: Supplies the token TTL.
        email: The submitted address.

    Returns:
        ``(user, plaintext token)``, or ``None`` if no active account matches.
        The caller **must** return an identical response either way — see the
        route handler. Answering "no account with that email" turns the reset
        form into a free account-enumeration tool.
    """
    user = await get_user_by_email(session, email.strip().lower())
    if user is None or not user.is_active:
        return None

    # Invalidate any outstanding links. Otherwise requesting a second reset
    # leaves the first one live, widening the window in which a link sitting in
    # a mailbox is still usable.
    await session.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=datetime.now(UTC))
    )

    raw = generate_token(32)
    session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.password_reset_ttl_seconds),
        )
    )
    record_audit(
        session,
        action="user.password_reset_requested",
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
    )
    return user, raw


async def consume_password_reset(
    session: AsyncSession,
    *,
    raw_token: str,
    new_password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> User:
    """Redeem a reset token and set a new password.

    Args:
        session: Active session.
        raw_token: The token from the emailed link.
        new_password: The replacement password.
        ip_address: Source IP for the audit entry.
        user_agent: Client user agent for the audit entry.

    Returns:
        The user whose password was changed.

    Raises:
        UnauthorizedError: If the token is unknown, already used, or expired.
            One message covers all three so a probe learns nothing.
    """
    result = await session.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(raw_token))
    )
    reset = result.scalar_one_or_none()

    now = datetime.now(UTC)
    if reset is None or reset.used_at is not None or reset.expires_at <= now:
        raise UnauthorizedError("This reset link is invalid or has expired. Request a new one.")

    user = await session.get(User, reset.user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("This reset link is invalid or has expired. Request a new one.")

    reset.used_at = now
    user.password_hash = await asyncio.to_thread(hash_password, new_password)
    await revoke_all_sessions(session, user.id, reason="password_reset")
    record_audit(
        session,
        action="user.password_reset_completed",
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return user


# -----------------------------------------------------------------------------
# Administration
# -----------------------------------------------------------------------------


async def admin_update_user(
    session: AsyncSession,
    settings: AuthSettings,
    *,
    actor: UUID,
    target_user_id: UUID,
    is_active: bool | None,
    role: str | None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> User:
    """Apply an admin's change to another account.

    Guard rails, each closing a specific failure mode:

    * **Role changes are limited to the allowlist.** Promotion to ``admin``
      requires the target's address to be in ``ADMIN_EMAILS``, which lives in
      the deployment environment. A stolen admin session alone is therefore not
      enough to create a second, persistent admin account.
    * **No self-modification.** An admin cannot change their own role or
      disable themselves — that prevents both accidental self-lockout and the
      "demote everyone including yourself" scenario that leaves the system with
      no administrator at all.
    * **Sessions are revoked on any change.** A demoted or disabled user must
      lose access now, not in fifteen minutes when their access token expires.

    Args:
        session: Active session.
        settings: Supplies the admin allowlist.
        actor: The acting admin's ID.
        target_user_id: The account being changed.
        is_active: New active state, or ``None`` to leave unchanged.
        role: New role, or ``None`` to leave unchanged.
        ip_address: Source IP for the audit entry.
        user_agent: Client user agent for the audit entry.

    Returns:
        The updated user.

    Raises:
        NotFoundError: If the target does not exist.
        ForbiddenError: For self-modification, or a promotion outside the
            allowlist.
    """
    if actor == target_user_id:
        raise ForbiddenError(
            "You cannot change your own role or status. Ask another administrator."
        )

    user = await get_user_by_id(session, target_user_id)
    changes: dict[str, Any] = {}

    if role is not None and role != user.role:
        if role == "admin" and not settings.is_admin_email(user.email):
            raise ForbiddenError(
                "This address is not on the administrator allowlist. "
                "Add it to ADMIN_EMAILS and redeploy before promoting."
            )
        changes["role"] = {"from": user.role, "to": role}
        user.role = role

    if is_active is not None and is_active != user.is_active:
        changes["is_active"] = {"from": user.is_active, "to": is_active}
        user.is_active = is_active

    if changes:
        await revoke_all_sessions(session, user.id, reason="admin_update")
        record_audit(
            session,
            action="user.admin_updated",
            actor_user_id=actor,
            target_type="user",
            target_id=str(user.id),
            ip_address=ip_address,
            user_agent=user_agent,
            metadata=changes,
        )
        log.info("admin_updated_user", actor=str(actor), target=str(user.id), changes=changes)

    return user


# -----------------------------------------------------------------------------
# Email verification
# -----------------------------------------------------------------------------


async def create_email_verification(
    session: AsyncSession, settings: AuthSettings, *, user: User
) -> str:
    """Issue a verification token for a user's current address.

    Any outstanding tokens are invalidated first. Requesting a second link
    otherwise leaves the first one live, widening the window in which an old
    message sitting in a mailbox is still usable.

    Args:
        session: Active session.
        settings: Supplies the token lifetime.
        user: Whose address to verify.

    Returns:
        The plaintext token. The only copy that will ever exist - the row holds
        a hash - so it must go straight into the email and nowhere else.
    """
    await session.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
    )

    raw = generate_token(32)
    session.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            email=user.email,
            expires_at=datetime.now(UTC)
            + timedelta(seconds=settings.email_verification_ttl_seconds),
        )
    )
    await session.flush()
    return raw


async def consume_email_verification(session: AsyncSession, *, raw_token: str) -> User:
    """Redeem a verification link and mark the address confirmed.

    Args:
        session: Active session.
        raw_token: The token from the emailed link.

    Returns:
        The user whose address was verified.

    Raises:
        UnauthorizedError: If the token is unknown, already used, expired, or
            was issued for an address the account no longer uses. One message
            covers all four, so a probe learns nothing.
    """
    result = await session.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == hash_token(raw_token)
        )
    )
    token = result.scalar_one_or_none()

    now = datetime.now(UTC)
    invalid = "This link is invalid or has expired. Request a new one."

    if token is None or token.used_at is not None or token.expires_at <= now:
        raise UnauthorizedError(invalid)

    user = await session.get(User, token.user_id)
    if user is None:
        raise UnauthorizedError(invalid)

    # The address must still be the one the link was issued for. Otherwise a
    # link sent to an old address would verify whatever the account was changed
    # to afterwards.
    if user.email != token.email:
        raise UnauthorizedError(invalid)

    token.used_at = now

    # Idempotent: clicking the link twice is normal (mail clients prefetch
    # links), and the second click should not look like a failure.
    if user.email_verified_at is None:
        user.email_verified_at = now
        record_audit(
            session,
            action="user.email_verified",
            actor_user_id=user.id,
            target_type="user",
            target_id=str(user.id),
        )
        log.info("email_verified", user_id=str(user.id))

    return user
