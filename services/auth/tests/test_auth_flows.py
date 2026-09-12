"""Integration tests for the auth service.

Each test states the security property it protects. When one fails, the failure
message should tell you what an attacker just became able to do.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from ecom_auth import service
from ecom_auth.models import LoginAttempt, RefreshToken, User

GOOD_PASSWORD = "marble-colonnade-77"


async def _register(client, email="shopper@example.com", password=GOOD_PASSWORD):
    """Register a user and return the response."""
    return await client.post(
        "/auth/register",
        json={"email": email, "password": password, "fullName": "Test Shopper"},
    )


async def _refresh_with(client, token: str):
    """Call /auth/refresh presenting `token` as the refresh cookie.

    Cookies are set on the client rather than passed per-request: httpx has
    deprecated per-request cookies because whether they persist afterwards is
    ambiguous, and being explicit about the jar is what these tests need anyway.
    """
    client.cookies.set("refresh_token", token)
    return await client.post("/auth/refresh")


class TestRegistration:
    async def test_creates_a_customer(self, client):
        response = await _register(client)
        assert response.status_code == 201
        body = response.json()
        assert body["user"]["email"] == "shopper@example.com"
        assert body["user"]["role"] == "customer"

    async def test_never_returns_the_password_hash(self, client):
        """The response model is explicit, so a new column cannot leak."""
        response = await _register(client)
        assert "passwordHash" not in str(response.json())
        assert "password" not in response.json()["user"]

    async def test_sets_httponly_cookies(self, client):
        """XSS must not be able to read the session token."""
        response = await _register(client)
        set_cookie = response.headers.get_list("set-cookie")
        access = next(c for c in set_cookie if c.startswith("access_token="))
        assert "HttpOnly" in access
        assert "SameSite=lax" in access.lower().replace("samesite=lax", "SameSite=lax")

    async def test_rejects_duplicate_email(self, client):
        await _register(client)
        response = await _register(client)
        assert response.status_code == 409

    async def test_email_is_case_insensitive(self, client):
        """CITEXT means Shopper@ and shopper@ are the same account, so a
        duplicate cannot be created by changing capitalisation."""
        await _register(client, email="shopper@example.com")
        response = await _register(client, email="SHOPPER@example.com")
        assert response.status_code == 409

    @pytest.mark.parametrize(
        "password",
        ["short", "password1234", "aaaaaaaaaaaaaa", "qwertyqwerty"],
    )
    async def test_rejects_weak_passwords(self, client, password):
        response = await _register(client, password=password)
        assert response.status_code == 422

    async def test_cannot_self_assign_admin_role(self, client):
        """`extra="forbid"` turns a mass-assignment attempt into a 422 rather
        than silently ignoring the field."""
        response = await client.post(
            "/auth/register",
            json={
                "email": "sneaky@example.com",
                "password": GOOD_PASSWORD,
                "role": "admin",
            },
        )
        assert response.status_code == 422


class TestLogin:
    async def test_succeeds_with_correct_credentials(self, client):
        await _register(client)
        response = await client.post(
            "/auth/login",
            json={"email": "shopper@example.com", "password": GOOD_PASSWORD},
        )
        assert response.status_code == 200

    async def test_identical_message_for_unknown_user_and_wrong_password(self, client):
        """Any difference here is an account-enumeration oracle."""
        await _register(client)
        wrong_password = await client.post(
            "/auth/login",
            json={"email": "shopper@example.com", "password": "definitely-wrong-99"},
        )
        unknown_user = await client.post(
            "/auth/login",
            json={"email": "ghost@example.com", "password": "definitely-wrong-99"},
        )
        assert wrong_password.status_code == unknown_user.status_code == 401
        assert (
            wrong_password.json()["error"]["message"]
            == unknown_user.json()["error"]["message"]
        )

    async def test_failed_attempts_are_persisted(self, client, db):
        """The row must survive the 401. If it does not, the lockout counter
        stays at zero and brute-force protection silently does nothing."""
        await _register(client)
        await client.post(
            "/auth/login",
            json={"email": "shopper@example.com", "password": "wrong-password-1"},
        )
        rows = (
            await db.execute(
                select(LoginAttempt).where(LoginAttempt.email == "shopper@example.com")
            )
        ).scalars().all()
        assert any(r.succeeded is False for r in rows)

    async def test_lockout_engages_after_threshold(self, client, settings):
        """Online brute force must become impractical."""
        await _register(client)
        for _ in range(settings.max_login_attempts):
            await client.post(
                "/auth/login",
                json={"email": "shopper@example.com", "password": "wrong-password-1"},
            )
        response = await client.post(
            "/auth/login",
            json={"email": "shopper@example.com", "password": GOOD_PASSWORD},
        )
        assert response.status_code == 401
        assert "Too many failed attempts" in response.json()["error"]["message"]

    async def test_inactive_account_cannot_sign_in(self, client, db):
        await _register(client)
        user = (
            await db.execute(select(User).where(User.email == "shopper@example.com"))
        ).scalar_one()
        user.is_active = False
        await db.flush()

        response = await client.post(
            "/auth/login",
            json={"email": "shopper@example.com", "password": GOOD_PASSWORD},
        )
        assert response.status_code == 401


class TestSessions:
    async def test_me_requires_authentication(self, client):
        assert (await client.get("/auth/me")).status_code == 401

    async def test_me_returns_the_current_user(self, client):
        await _register(client)
        response = await client.get("/auth/me")
        assert response.status_code == 200
        assert response.json()["email"] == "shopper@example.com"

    async def test_refresh_rotates_the_token(self, client, db):
        """The old token must stop working the instant a new one is issued."""
        await _register(client)
        first = client.cookies.get("refresh_token")
        assert first

        response = await _refresh_with(client, first)
        assert response.status_code == 200
        second = response.cookies.get("refresh_token")
        assert second and second != first

    async def test_reuse_revokes_every_session(self, client, db):
        """A stolen refresh token must buy at most one request. Replaying a
        consumed token means either a replay or a theft; since we cannot tell,
        the whole family is revoked."""
        await _register(client)
        first = client.cookies.get("refresh_token")
        rotated = (await _refresh_with(client, first)).cookies.get("refresh_token")
        assert rotated

        replay = await _refresh_with(client, first)
        assert replay.status_code == 401

        # The legitimate successor is now dead too.
        after = await _refresh_with(client, rotated)
        assert after.status_code == 401

    async def test_logout_revokes_the_token(self, client, db):
        await _register(client)
        token = client.cookies.get("refresh_token")
        assert (await client.post("/auth/logout")).status_code == 200

        response = await _refresh_with(client, token)
        assert response.status_code == 401

    async def test_password_change_revokes_all_sessions(self, client, db):
        """Changing a password is what a user does when they suspect
        compromise; an attacker's session must not survive it."""
        await _register(client)
        token = client.cookies.get("refresh_token")

        response = await client.post(
            "/auth/password/change",
            json={"currentPassword": GOOD_PASSWORD, "newPassword": "new-forum-romanum-8"},
        )
        assert response.status_code == 200

        after = await _refresh_with(client, token)
        assert after.status_code == 401

    async def test_password_change_requires_the_current_password(self, client):
        """Stops someone at a borrowed keyboard from taking the account over."""
        await _register(client)
        response = await client.post(
            "/auth/password/change",
            json={"currentPassword": "not-the-password", "newPassword": "new-forum-romanum-8"},
        )
        assert response.status_code == 401


class TestPasswordReset:
    async def test_response_is_identical_for_unknown_addresses(self, client):
        """This endpoint is unauthenticated. Any difference in the response
        turns it into a customer-list extraction tool."""
        await _register(client)
        known = await client.post(
            "/auth/password/forgot", json={"email": "shopper@example.com"}
        )
        unknown = await client.post(
            "/auth/password/forgot", json={"email": "nobody@example.com"}
        )
        assert known.status_code == unknown.status_code == 200
        assert known.json() == unknown.json()

    async def test_token_is_single_use(self, client, db, settings):
        await _register(client)
        result = await service.create_password_reset(
            db, settings, email="shopper@example.com"
        )
        assert result is not None
        _, raw = result
        await db.flush()

        first = await client.post(
            "/auth/password/reset",
            json={"token": raw, "newPassword": "aqueduct-lattice-31"},
        )
        assert first.status_code == 200

        second = await client.post(
            "/auth/password/reset",
            json={"token": raw, "newPassword": "different-pass-4422"},
        )
        assert second.status_code == 401

    async def test_rejects_an_unknown_token(self, client):
        response = await client.post(
            "/auth/password/reset",
            json={"token": "x" * 40, "newPassword": "aqueduct-lattice-31"},
        )
        assert response.status_code == 401


class TestAdminAuthorisation:
    async def _make_admin(self, db, client):
        """Register a user and promote them directly in the database."""
        await _register(client, email="admin@example.com")
        user = (
            await db.execute(select(User).where(User.email == "admin@example.com"))
        ).scalar_one()
        user.role = "admin"
        await db.flush()
        # Re-authenticate so the new role is in the access token.
        await client.post(
            "/auth/login",
            json={"email": "admin@example.com", "password": GOOD_PASSWORD},
        )
        return user

    async def test_anonymous_gets_401(self, client):
        assert (await client.get("/auth/admin/users")).status_code == 401

    async def test_customer_gets_403(self, client):
        """401 vs 403 is a real distinction: the customer's credentials are
        fine, so retrying with fresh ones would not help."""
        await _register(client)
        assert (await client.get("/auth/admin/users")).status_code == 403

    async def test_admin_can_list_users(self, client, db):
        await self._make_admin(db, client)
        response = await client.get("/auth/admin/users")
        assert response.status_code == 200
        assert response.json()["total"] >= 1

    async def test_admin_cannot_change_their_own_role(self, client, db):
        """Prevents both self-lockout and an admin quietly entrenching."""
        admin = await self._make_admin(db, client)
        response = await client.patch(
            f"/auth/admin/users/{admin.id}", json={"role": "customer"}
        )
        assert response.status_code == 403

    async def test_promotion_requires_the_allowlist(self, client, db, settings):
        """A compromised admin session alone must not be able to mint a second,
        persistent admin: the target address must also be in ADMIN_EMAILS,
        which lives in the deployment environment."""
        await self._make_admin(db, client)
        await _register(client, email="outsider@example.com")
        outsider = (
            await db.execute(select(User).where(User.email == "outsider@example.com"))
        ).scalar_one()
        # Sign back in as the admin (registering switched the cookie).
        await client.post(
            "/auth/login",
            json={"email": "admin@example.com", "password": GOOD_PASSWORD},
        )

        response = await client.patch(
            f"/auth/admin/users/{outsider.id}", json={"role": "admin"}
        )
        assert response.status_code == 403
        assert "allowlist" in response.json()["error"]["message"]

    async def test_deactivating_a_user_revokes_their_sessions(self, client, db):
        await _register(client, email="victim@example.com")
        victim = (
            await db.execute(select(User).where(User.email == "victim@example.com"))
        ).scalar_one()
        await self._make_admin(db, client)

        response = await client.patch(
            f"/auth/admin/users/{victim.id}", json={"isActive": False}
        )
        assert response.status_code == 200

        live = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == victim.id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        ).scalars().all()
        assert live == []


class TestSqlInjection:
    async def test_admin_search_is_parameterised(self, client, db):
        """The classic payload must be treated as a search string, not SQL."""
        await _register(client, email="admin@example.com")
        user = (
            await db.execute(select(User).where(User.email == "admin@example.com"))
        ).scalar_one()
        user.role = "admin"
        await db.flush()
        await client.post(
            "/auth/login",
            json={"email": "admin@example.com", "password": GOOD_PASSWORD},
        )

        response = await client.get(
            "/auth/admin/users", params={"search": "'; DROP TABLE users; --"}
        )
        assert response.status_code == 200
        assert response.json()["items"] == []

        # The table is still there.
        assert (await db.execute(select(User))).scalars().first() is not None
