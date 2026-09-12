"""Configuration for the payments service."""

from __future__ import annotations

from ecom_shared.config import ServiceSettings
from pydantic import SecretStr


class PaymentSettings(ServiceSettings):
    """Settings for Stripe payment intents, webhooks and refunds.

    Attributes:
        stripe_secret_key: Server-side API key (``sk_test_…`` / ``sk_live_…``).
            A `SecretStr`, so it cannot be printed by accident — an API key in
            a log line is a compromised Stripe account.
        stripe_webhook_secret: Signing secret for webhook verification. Without
            it, anyone who can reach the webhook URL could POST a fabricated
            "payment succeeded" event and receive goods for free. The service
            refuses to process unsigned events, so this is mandatory.
        stripe_currency: Default ISO 4217 currency, lowercase as Stripe expects.
    """

    service_name: str = "payments"
    db_schema: str = "payments"

    stripe_secret_key: SecretStr = SecretStr("")
    stripe_webhook_secret: SecretStr = SecretStr("")
    stripe_currency: str = "usd"

    orders_url: str = "http://orders:8000"
    notifications_url: str = "http://notifications:8000"

    @property
    def stripe_configured(self) -> bool:
        """Whether a usable Stripe key is present.

        Checked at startup so a missing key is a clear log line at boot rather
        than a confusing 500 the first time someone tries to check out.
        """
        key = self.stripe_secret_key.get_secret_value()
        return bool(key) and not key.startswith("sk_test_CHANGE")
