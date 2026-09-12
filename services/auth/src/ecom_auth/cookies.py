"""Setting and clearing the session cookies.

Why cookies at all, rather than putting the JWT in `localStorage` and sending
an `Authorization` header:

    `localStorage` is readable by every script running on the page. One
    compromised npm dependency, one reflected XSS, and every visitor's token is
    exfiltrated. An `httpOnly` cookie is invisible to JavaScript entirely, so
    the same bug yields nothing — the attacker can make requests *as* the user
    while their script runs, but cannot steal a credential that outlives the
    page.

    The cost is CSRF exposure, since browsers attach cookies automatically.
    That is covered by `SameSite=Lax` plus the double-submit token enforced in
    the gateway. It is a much easier problem to solve completely than XSS is.
"""

from __future__ import annotations

from ecom_shared.identity import (
    ACCESS_COOKIE_NAME,
    ACCESS_COOKIE_NAME_INSECURE,
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_NAME_INSECURE,
)
from fastapi import Response

#: Default path the refresh cookie is scoped to.
#:
#: Narrowing it means the long-lived credential is only attached to the one
#: request that needs it, instead of riding along on every image and API call.
#: Fewer requests carrying it is fewer chances to leak it.
#:
#: The value must be the path **as the browser sees it**, which is the gateway's
#: public route, not the auth service's internal one. Cookie paths are matched
#: by the browser against the URL it requested, so a mismatch does not raise an
#: error anywhere — the cookie is simply never sent, and refresh fails with a
#: puzzling 401. It is overridable via `REFRESH_COOKIE_PATH` so changing the
#: gateway's prefix does not silently break sign-in.
DEFAULT_REFRESH_COOKIE_PATH = "/api/auth"

#: Name of the CSRF cookie. Intentionally NOT httpOnly — the frontend has to
#: read it to echo it back in a header. That is safe: the cookie is not a
#: credential on its own, it only proves the request came from a page that
#: could read same-origin cookies, which a cross-site attacker cannot do.
CSRF_COOKIE_NAME = "csrf_token"


def set_session_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    csrf_token: str,
    access_ttl: int,
    refresh_ttl: int,
    secure: bool,
    refresh_path: str = DEFAULT_REFRESH_COOKIE_PATH,
) -> None:
    """Attach the access, refresh and CSRF cookies to `response`.

    Args:
        response: The response being returned to the browser.
        access_token: Short-lived JWT.
        refresh_token: Long-lived opaque token.
        csrf_token: Double-submit value the client will echo in a header.
        access_ttl: Access cookie lifetime, seconds.
        refresh_ttl: Refresh cookie lifetime, seconds.
        secure: Send cookies over HTTPS only. Must be ``True`` in production;
            ``False`` locally, because a Secure cookie is silently dropped over
            plain HTTP and you would spend an afternoon wondering why login
            "works" but every subsequent request is anonymous.
        refresh_path: Public path the refresh cookie is scoped to.

    Note:
        The ``__Host-`` cookie name prefix is only accepted by browsers when the
        cookie is Secure, so the insecure names are used in local development.
        Everything else about the cookies is identical.
    """
    access_name = ACCESS_COOKIE_NAME if secure else ACCESS_COOKIE_NAME_INSECURE
    refresh_name = REFRESH_COOKIE_NAME if secure else REFRESH_COOKIE_NAME_INSECURE

    response.set_cookie(
        key=access_name,
        value=access_token,
        max_age=access_ttl,
        httponly=True,
        secure=secure,
        # "lax" sends the cookie on top-level navigations but not on
        # cross-site POSTs, which blocks the classic hidden-form CSRF outright.
        # "strict" would additionally break returning from a Stripe redirect
        # still signed in.
        samesite="lax",
        path="/",
    )

    response.set_cookie(
        key=refresh_name,
        value=refresh_token,
        max_age=refresh_ttl,
        httponly=True,
        secure=secure,
        samesite="lax",
        path=refresh_path,
    )

    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=refresh_ttl,
        httponly=False,  # deliberately readable; see module docstring
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(
    response: Response,
    *,
    secure: bool,
    refresh_path: str = DEFAULT_REFRESH_COOKIE_PATH,
) -> None:
    """Remove every session cookie.

    Both the ``__Host-`` and plain variants are deleted regardless of the
    current `secure` setting. Toggling `COOKIE_SECURE` between deployments
    would otherwise strand a cookie under the other name, leaving a user who
    clicked "sign out" still holding a valid token.

    Args:
        response: The logout response.
        secure: Current cookie security setting, used for the delete attributes.
        refresh_path: Path the refresh cookie was scoped to. A delete only
            matches a cookie with the same path, so this must agree with what
            `set_session_cookies` used.
    """
    for name in (ACCESS_COOKIE_NAME, ACCESS_COOKIE_NAME_INSECURE):
        response.delete_cookie(name, path="/", secure=secure, httponly=True, samesite="lax")
    for name in (REFRESH_COOKIE_NAME, REFRESH_COOKIE_NAME_INSECURE):
        response.delete_cookie(
            name, path=refresh_path, secure=secure, httponly=True, samesite="lax"
        )
    response.delete_cookie(CSRF_COOKIE_NAME, path="/", secure=secure, samesite="lax")
