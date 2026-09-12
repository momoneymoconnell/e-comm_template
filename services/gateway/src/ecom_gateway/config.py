"""Configuration for the API gateway."""

from __future__ import annotations

from ecom_shared.config import ServiceSettings


class GatewaySettings(ServiceSettings):
    """Settings for routing, rate limiting and CSRF.

    Attributes:
        rate_limit_requests: Requests allowed per client per window.
        rate_limit_window_seconds: Length of the window.
        cookie_secure: Whether the CSRF cookie is marked Secure.
    """

    service_name: str = "gateway"
    #: The gateway owns no tables, so it never opens a database connection.
    db_schema: str = "public"

    auth_url: str = "http://auth:8000"
    catalog_url: str = "http://catalog:8000"
    orders_url: str = "http://orders:8000"
    payments_url: str = "http://payments:8000"
    analytics_url: str = "http://analytics:8000"
    notifications_url: str = "http://notifications:8000"

    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60

    cookie_secure: bool = False

    @property
    def routes(self) -> dict[str, str]:
        """Map the first path segment after ``/api/`` to a service base URL.

        A closed mapping, not a pattern. The gateway can only ever reach the
        six services named here, so a path-traversal attempt or a crafted
        prefix cannot make it proxy to an arbitrary host — server-side request
        forgery through a permissive proxy is a well-trodden attack.
        """
        return {
            "auth": self.auth_url,
            "catalog": self.catalog_url,
            "orders": self.orders_url,
            "payments": self.payments_url,
            "analytics": self.analytics_url,
            "notifications": self.notifications_url,
        }
