"""Who is calling, and are they allowed to do this.

**Trust model.** Each service verifies the access token's signature itself,
using the shared ``JWT_SECRET_KEY``. The gateway does not tell a service who
the caller is — it merely forwards the token.

That distinction matters. The tempting alternative is for the gateway to
validate once and pass ``X-User-Id: <uuid>`` downstream. It is faster, and it
is exactly how internal services get compromised: the moment anything other
than the gateway can open a connection to the orders service — a misconfigured
port mapping, a compromised sidecar, a developer with `kubectl port-forward` —
that header becomes a "log in as anyone" button. Verifying the signature at
every hop costs about 20 microseconds and removes the possibility entirely.

Where the token comes from, in priority order:

1. The ``__Host-access_token`` cookie — how the browser authenticates.
   ``httpOnly`` means JavaScript cannot read it, so an XSS bug cannot exfiltrate
   the session.
2. The ``Authorization: Bearer <jwt>`` header — for service-to-service calls
   and for anyone testing with curl.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Request

from ecom_shared.errors import ForbiddenError, UnauthorizedError
from ecom_shared.security import decode_jwt

#: Name of the cookie holding the access token.
#:
#: The ``__Host-`` prefix is enforced by browsers: a cookie with this prefix is
#: only accepted if it is Secure, has no Domain attribute, and has Path=/. That
#: makes it impossible for a sibling subdomain (or an attacker who obtains a
#: certificate for one) to overwrite your session cookie — a real attack class
#: called cookie tossing. Browsers require HTTPS for it, so over plain-HTTP
#: local development we fall back to the unprefixed name.
ACCESS_COOKIE_NAME = "__Host-access_token"
ACCESS_COOKIE_NAME_INSECURE = "access_token"

#: Refresh token cookie. Scoped to the refresh endpoint's path only, so it is
#: not sent on every request and cannot be stolen by a bug on an unrelated route.
REFRESH_COOKIE_NAME = "__Host-refresh_token"
REFRESH_COOKIE_NAME_INSECURE = "refresh_token"

Role = Literal["customer", "admin"]


@dataclass(frozen=True, slots=True)
class CallerIdentity:
    """The verified identity behind the current request.

    Frozen because a handler must never be able to mutate its own caller's role
    partway through — "privilege escalation by assignment" is a real bug, and
    immutability makes it a `FrozenInstanceError` instead.

    Attributes:
        user_id: The user's primary key, from the ``sub`` claim.
        email: The user's email at the time the token was issued.
        role: ``"customer"`` or ``"admin"``.
        token_id: The ``jti`` claim, for correlating a request with the exact
            session that made it during an audit.
    """

    user_id: UUID
    email: str
    role: Role
    token_id: str

    @property
    def is_admin(self) -> bool:
        """Whether this caller holds the admin role."""
        return self.role == "admin"


def _extract_token(request: Request) -> str | None:
    """Pull the raw access token out of the request, cookie first."""
    for name in (ACCESS_COOKIE_NAME, ACCESS_COOKIE_NAME_INSECURE):
        if token := request.cookies.get(name):
            return token

    header = request.headers.get("Authorization", "")
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() == "bearer" and credentials:
        return credentials.strip()
    return None


def _identity_from_request(request: Request) -> CallerIdentity | None:
    """Verify the token on `request` and build a `CallerIdentity`.

    Args:
        request: The incoming request.

    Returns:
        The verified identity, or ``None`` if no token was supplied.

    Raises:
        UnauthorizedError: If a token was supplied but is invalid, expired, or
            of the wrong type.
    """
    token = _extract_token(request)
    if token is None:
        return None

    # `settings` is attached to app.state by create_service_app().
    settings = request.app.state.settings
    claims = decode_jwt(
        token,
        secret_key=settings.jwt_secret_key.get_secret_value(),
        expected_type="access",
        algorithm=settings.jwt_algorithm,
    )

    role = claims.get("role", "customer")
    if role not in ("customer", "admin"):
        # An unrecognised role means a token we did not issue, or one issued by
        # an older/newer version of the service. Fail closed.
        raise UnauthorizedError("Invalid authentication credentials.")

    try:
        user_id = UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("Invalid authentication credentials.") from exc

    return CallerIdentity(
        user_id=user_id,
        email=claims.get("email", ""),
        role=role,
        token_id=claims.get("jti", ""),
    )


async def optional_user(request: Request) -> CallerIdentity | None:
    """Dependency: the caller's identity if signed in, otherwise ``None``.

    For endpoints that serve everyone but behave differently when signed in —
    a product page that shows a wishlist button, a cart that can be anonymous.

    Args:
        request: Injected by FastAPI.

    Returns:
        The identity, or ``None`` for an anonymous caller.
    """
    return _identity_from_request(request)


async def require_user(request: Request) -> CallerIdentity:
    """Dependency: require any signed-in user.

    Args:
        request: Injected by FastAPI.

    Returns:
        The verified caller identity.

    Raises:
        UnauthorizedError: If the caller is not signed in.
    """
    identity = _identity_from_request(request)
    if identity is None:
        raise UnauthorizedError("Sign in to continue.")
    return identity


async def require_admin(
    identity: Annotated[CallerIdentity, Depends(require_user)],
) -> CallerIdentity:
    """Dependency: require the admin role.

    Guards every ``/admin`` route. Note this builds on `require_user`, so an
    anonymous caller gets 401 ("sign in") while a signed-in customer gets 403
    ("not allowed") — the correct and distinct answers.

    Args:
        identity: The verified caller, injected by `require_user`.

    Returns:
        The identity, now known to be an admin.

    Raises:
        ForbiddenError: If the caller is authenticated but not an admin.
    """
    if not identity.is_admin:
        raise ForbiddenError("Administrator access required.")
    return identity


#: Convenient aliases so route signatures stay short and readable:
#:
#:     async def my_route(admin: AdminUser) -> ...:
CurrentUser = Annotated[CallerIdentity, Depends(require_user)]
AdminUser = Annotated[CallerIdentity, Depends(require_admin)]
MaybeUser = Annotated[CallerIdentity | None, Depends(optional_user)]


async def require_service(request: Request) -> str:
    """Dependency: require a valid internal service token.

    Guards ``/internal/*`` routes — endpoints that exist for other services and
    must never be reachable by a browser. A user's access token is rejected
    here, because its ``typ`` is ``access`` and this requires ``service``.

    Note:
        This authenticates the *caller*, not a user. It is not a substitute for
        network isolation: internal ports are bound to localhost in compose and
        should sit behind a private network in a real deployment. The token is
        the second layer, so that reaching the port is not the same as being
        authorised to use it.

    Args:
        request: Injected by FastAPI.

    Returns:
        The calling service's name.

    Raises:
        UnauthorizedError: If the token is missing, invalid, or not a service
            token.
    """
    header = request.headers.get("Authorization", "")
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "bearer" or not credentials:
        raise UnauthorizedError("Internal endpoint requires a service token.")

    settings = request.app.state.settings
    claims = decode_jwt(
        credentials.strip(),
        secret_key=settings.jwt_secret_key.get_secret_value(),
        expected_type="service",
        algorithm=settings.jwt_algorithm,
    )
    caller = claims.get("svc")
    if not caller:
        raise UnauthorizedError("Invalid service token.")
    return str(caller)


#: Route alias for internal, service-to-service endpoints.
ServiceCaller = Annotated[str, Depends(require_service)]
