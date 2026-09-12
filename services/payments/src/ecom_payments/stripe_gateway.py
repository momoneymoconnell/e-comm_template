"""The only module that talks to Stripe.

Isolating the SDK here has two payoffs: the rest of the service is testable
without network access or API keys, and swapping payment providers later means
rewriting one file rather than auditing the whole codebase for Stripe calls.

Every Stripe call is wrapped so their exception hierarchy becomes our
`AppError` family. A declined card is a 402-ish business outcome the customer
must see; a rate limit is a 502 they should retry; an authentication error is
our own misconfiguration and must never be shown to them.
"""

from __future__ import annotations

from typing import Any

import stripe
from ecom_shared.errors import AppError, UpstreamError, ValidationFailedError
from ecom_shared.logging import get_logger

from ecom_payments.config import PaymentSettings

log = get_logger(__name__)


class StripeGateway:
    """A thin, typed wrapper over the Stripe SDK."""

    def __init__(self, settings: PaymentSettings) -> None:
        """Configure the SDK.

        Args:
            settings: Supplies the API key and default currency.
        """
        self.settings = settings
        self._client = stripe.StripeClient(
            api_key=settings.stripe_secret_key.get_secret_value() or "sk_test_unset",
            # Pinning the API version means Stripe rolling out a breaking
            # change does not break checkout without a deploy on our side.
            stripe_version="2024-11-20.acacia",
        )

    async def create_payment_intent(
        self,
        *,
        amount_cents: int,
        currency: str,
        order_id: str,
        order_number: str,
        email: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Create a payment intent.

        Args:
            amount_cents: Amount in minor units, computed server-side.
            currency: ISO 4217, lowercase.
            order_id: Our order ID, attached as metadata so a webhook can be
                traced back even if our own lookup fails.
            order_number: Human-readable reference, used as the statement
                descriptor suffix so the customer recognises the charge on
                their bank statement and does not dispute it.
            email: Where Stripe sends its receipt.
            idempotency_key: Stripe deduplicates on this. If the request times
                out and we retry, Stripe returns the *original* intent instead
                of creating a second one — which is the difference between one
                charge and two.

        Returns:
            The created intent as a dict.

        Raises:
            UpstreamError: If Stripe is unreachable or misconfigured.
            ValidationFailedError: If Stripe rejects the request parameters.
        """
        try:
            intent = self._client.payment_intents.create(
                params={
                    "amount": amount_cents,
                    "currency": currency.lower(),
                    "receipt_email": email,
                    # Lets Stripe show whichever methods are enabled on the
                    # account and appropriate for the customer's country,
                    # without us hard-coding a list that goes stale.
                    "automatic_payment_methods": {"enabled": True},
                    "metadata": {
                        "order_id": order_id,
                        "order_number": order_number,
                    },
                    "description": f"Order {order_number}",
                },
                options={"idempotency_key": idempotency_key},
            )
        except stripe.InvalidRequestError as exc:
            log.error("stripe_invalid_request", error=str(exc))
            raise ValidationFailedError("That payment could not be set up.") from exc
        except stripe.AuthenticationError as exc:
            # Our key is wrong or revoked. The customer must never see this.
            log.error("stripe_authentication_failed", hint="Check STRIPE_SECRET_KEY")
            raise UpstreamError("Payments are temporarily unavailable.") from exc
        except stripe.StripeError as exc:
            log.error("stripe_error", error=str(exc))
            raise UpstreamError("Payments are temporarily unavailable.") from exc

        # `to_dict()` rather than `dict(intent)`: Stripe's objects are not dict
        # subclasses. The rest of the service treats Stripe responses as opaque
        # mappings, which keeps the SDK's own types out of our business logic
        # and makes swapping providers a change to this file alone.
        return intent.to_dict()

    async def refund(
        self, *, payment_intent_id: str, amount_cents: int | None, reason: str | None
    ) -> dict[str, Any]:
        """Refund a payment, in whole or in part.

        Args:
            payment_intent_id: The intent to refund.
            amount_cents: Amount to return; ``None`` refunds everything
                captured.
            reason: Free text recorded against the refund.

        Returns:
            The refund object as a dict.

        Raises:
            ValidationFailedError: If Stripe rejects it — typically because the
                payment was already fully refunded.
            UpstreamError: For any other Stripe failure.
        """
        # Typed as the SDK's TypedDict so the checker accepts it; built
        # incrementally because the optional keys depend on the caller.
        params: Any = {"payment_intent": payment_intent_id}
        if amount_cents is not None:
            params["amount"] = amount_cents
        if reason:
            params["metadata"] = {"reason": reason[:500]}

        try:
            refund = self._client.refunds.create(params=params)
        except stripe.InvalidRequestError as exc:
            log.error("stripe_refund_rejected", error=str(exc))
            raise ValidationFailedError(
                "Stripe rejected that refund. It may already have been refunded."
            ) from exc
        except stripe.StripeError as exc:
            log.error("stripe_refund_failed", error=str(exc))
            raise UpstreamError("Could not process the refund. Try again shortly.") from exc

        return refund.to_dict()

    def verify_webhook(self, payload: bytes, signature_header: str) -> dict[str, Any]:
        """Verify a webhook signature and return the decoded event.

        **This is the security boundary of the entire payment flow.** The
        webhook endpoint is public — it must be, for Stripe to reach it — so
        without signature verification anyone could POST
        ``{"type": "payment_intent.succeeded"}`` and have orders marked paid
        and goods dispatched for free.

        `construct_event` checks an HMAC over the raw body using the endpoint's
        signing secret, and enforces a timestamp tolerance so a signature
        captured from an old request cannot be replayed later.

        Args:
            payload: The **raw** request body, exactly as received. It must not
                be parsed and re-serialised first: the signature covers the
                literal bytes, and re-encoding changes key order and whitespace,
                which breaks verification for reasons that are miserable to
                debug.
            signature_header: The ``Stripe-Signature`` header.

        Returns:
            The verified event.

        Raises:
            AppError: If the signature is missing, malformed, or does not
                verify. The caller returns 400, and no business logic runs.
        """
        secret = self.settings.stripe_webhook_secret.get_secret_value()
        if not secret or secret.startswith("whsec_CHANGE"):
            log.error(
                "stripe_webhook_secret_missing",
                hint="Set STRIPE_WEBHOOK_SECRET. Unsigned webhooks are rejected.",
            )
            raise UpstreamError("Webhook processing is not configured.")

        try:
            event = stripe.Webhook.construct_event(payload, signature_header, secret)
        except ValueError as exc:
            log.warning("stripe_webhook_malformed")
            raise _BadWebhook("Malformed webhook payload.") from exc
        except stripe.SignatureVerificationError as exc:
            # Either a misconfigured secret or someone forging events. Both are
            # worth alerting on.
            log.error("stripe_webhook_signature_invalid")
            raise _BadWebhook("Webhook signature verification failed.") from exc

        return dict(event)


class _BadWebhook(AppError):
    """400 — a webhook that failed verification. Never processed."""

    status_code = 400
    code = "invalid_webhook"
