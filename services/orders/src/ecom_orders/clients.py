"""HTTP clients for the services orders depends on.

Every outbound call is wrapped here rather than scattered through the business
logic, so timeouts, retries and error translation are consistent — and so the
whole set of dependencies is visible in one file.

Three rules applied to every call:

* **A timeout is always set.** httpx defaults to *no* timeout; one slow
  dependency would otherwise hold a connection open until the client gives up,
  and under load that exhausts the pool and takes the service down with it.
* **Failures become `UpstreamError`.** The caller does not need to know whether
  it was DNS, a connection reset or a 503 — only that a dependency failed,
  which is a 502 to our own client, not a 500.
* **A service token is attached.** Internal endpoints reject anything else.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from ecom_shared.errors import AppError, ConflictError, UpstreamError
from ecom_shared.logging import get_logger, get_request_id
from ecom_shared.middleware import REQUEST_ID_HEADER
from ecom_shared.security import create_service_token

from ecom_orders.config import OrderSettings

log = get_logger(__name__)

#: Timeout for internal calls. Generous enough for a cold connection pool,
#: short enough that a wedged dependency fails fast instead of piling up.
INTERNAL_TIMEOUT = httpx.Timeout(5.0, connect=2.0)


class ServiceClient:
    """Base client carrying auth and request correlation.

    Attributes:
        settings: Provides the signing key and service URLs.
    """

    def __init__(self, settings: OrderSettings) -> None:
        """Store settings.

        Args:
            settings: The orders service settings.
        """
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        """Build headers for an internal call.

        Includes the incoming request's ID, so one customer action can be
        traced across every service it touches by filtering logs on a single
        value.

        Returns:
            Authorization and correlation headers.
        """
        token = create_service_token(
            service_name="orders",
            secret_key=self.settings.jwt_secret_key.get_secret_value(),
            ttl_seconds=60,
        )
        return {
            "Authorization": f"Bearer {token}",
            REQUEST_ID_HEADER: get_request_id(),
        }

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST JSON to an internal endpoint and return the decoded response.

        Args:
            url: Absolute URL.
            payload: JSON body.

        Returns:
            The decoded response body.

        Raises:
            ConflictError: If the dependency returned 409, which for our
                purposes means a business rule was violated (out of stock) and
                the message is safe to show the customer.
            UpstreamError: For any other failure.
        """
        try:
            async with httpx.AsyncClient(timeout=INTERNAL_TIMEOUT) as client:
                response = await client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            log.error("internal_call_failed", url=url, error=str(exc))
            raise UpstreamError("A required service is unavailable. Please try again.") from exc

        if response.status_code == 409:
            # Propagate the dependency's own message; it was written for the
            # end user ("Only 2 left of PH-01-M").
            body = _safe_json(response)
            message = body.get("error", {}).get("message", "That item is unavailable.")
            raise ConflictError(message, details=body.get("error", {}).get("details", {}))

        if response.status_code >= 400:
            log.error(
                "internal_call_error",
                url=url,
                status=response.status_code,
                body=response.text[:500],
            )
            raise UpstreamError("A required service returned an error. Please try again.")

        return _safe_json(response)


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    """Decode a JSON body, tolerating a non-JSON error page.

    A dependency behind a misconfigured proxy can return HTML with a 502. That
    should surface as our own clear error, not a `JSONDecodeError` traceback.

    Args:
        response: The HTTP response.

    Returns:
        The decoded object, or ``{}`` if the body was not JSON.
    """
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {"data": data}


class CatalogClient(ServiceClient):
    """Calls into the catalog service."""

    async def price_variants(self, variant_ids: list[UUID]) -> list[dict[str, Any]]:
        """Fetch authoritative prices and availability.

        This is the guard against cart tampering: the browser sends variant IDs
        and quantities, and the price comes from here.

        Args:
            variant_ids: The variants to price.

        Returns:
            One entry per variant that exists. Missing IDs are absent, and the
            caller must treat that as a failed checkout rather than skipping
            the line.
        """
        url = f"{self.settings.catalog_url}/internal/catalog/variants/price"
        payload = {"variantIds": [str(v) for v in variant_ids]}
        try:
            async with httpx.AsyncClient(timeout=INTERNAL_TIMEOUT) as client:
                response = await client.post(url, json=payload, headers=self._headers())
                response.raise_for_status()
                return list(response.json())
        except httpx.HTTPError as exc:
            log.error("catalog_price_failed", error=str(exc))
            raise UpstreamError("Could not price your cart. Please try again.") from exc

    async def reserve_inventory(self, lines: list[tuple[UUID, int]]) -> None:
        """Reserve stock for a set of lines.

        Args:
            lines: ``(variant_id, quantity)`` pairs.

        Raises:
            ConflictError: If any line is out of stock.
        """
        await self._post(
            f"{self.settings.catalog_url}/internal/catalog/inventory/reserve",
            {"lines": [{"variantId": str(v), "quantity": q} for v, q in lines]},
        )

    async def release_inventory(self, lines: list[tuple[UUID, int]]) -> None:
        """Return reserved stock.

        Never raises. This runs on the compensating path of a failed checkout,
        where throwing would turn one problem into two — the order fails *and*
        the stock stays locked away. A failure is logged loudly instead, and
        the `orders.stock_release_failed` log line is worth alerting on.

        Args:
            lines: ``(variant_id, quantity)`` pairs to return.
        """
        try:
            await self._post(
                f"{self.settings.catalog_url}/internal/catalog/inventory/release",
                {"lines": [{"variantId": str(v), "quantity": q} for v, q in lines]},
            )
        except AppError as exc:
            log.error(
                "stock_release_failed",
                error=str(exc),
                lines=[(str(v), q) for v, q in lines],
                hint="Stock is reserved against an order that will not complete. "
                "Reconcile manually.",
            )


class PaymentsClient(ServiceClient):
    """Calls into the payments service."""

    async def create_payment_intent(
        self,
        *,
        order_id: UUID,
        order_number: str,
        amount_cents: int,
        currency: str,
        email: str,
    ) -> dict[str, Any]:
        """Create a Stripe payment intent for an order.

        Args:
            order_id: Our order's ID, attached to the intent as metadata so a
                webhook can find its way back.
            order_number: Human-readable reference, shown on the card statement.
            amount_cents: Total to charge, in minor units.
            currency: ISO 4217, lowercase.
            email: Where Stripe sends its receipt.

        Returns:
            ``{"paymentIntentId": ..., "clientSecret": ...}``. The client
            secret goes to the browser, which uses it to confirm the payment
            directly with Stripe — card details never reach our servers.
        """
        return await self._post(
            f"{self.settings.payments_url}/internal/payments/intents",
            {
                "orderId": str(order_id),
                "orderNumber": order_number,
                "amountCents": amount_cents,
                "currency": currency,
                "email": email,
            },
        )


class NotificationsClient(ServiceClient):
    """Calls into the notifications service."""

    async def send(self, *, template: str, to: str, context: dict[str, Any]) -> None:
        """Queue a transactional email.

        Never raises: a receipt that fails to send must not roll back a paid
        order. The notifications service persists to an outbox and retries, so
        a transient failure here is recoverable on its own.

        Args:
            template: Template name, e.g. ``"order_confirmation"``.
            to: Recipient address.
            context: Template variables.
        """
        try:
            await self._post(
                f"{self.settings.notifications_url}/notifications/send",
                {"template": template, "to": to, "context": context},
            )
        except AppError as exc:
            log.error("notification_dispatch_failed", template=template, error=str(exc))
