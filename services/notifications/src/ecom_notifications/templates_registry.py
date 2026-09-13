"""Email templates.

Templates live in Python rather than in files so the service image stays a
single artefact with nothing to mount, and so a missing template is an import
error at startup rather than a runtime failure on the checkout path.

Every template renders **both** an HTML and a plain-text body. That is not
politeness: a message with no text alternative scores badly with spam filters,
and some corporate mail gateways strip HTML entirely, leaving the recipient
with a blank email where their order confirmation should be.

Jinja2 autoescaping is on. Order data — names, addresses — is user-supplied, and
a customer whose name contains ``<script>`` should receive their own name back,
not an injected payload in whatever webmail client they use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ecom_shared.errors import ValidationFailedError
from jinja2 import Environment, select_autoescape

#: Shared Jinja environment.
#:
#: `select_autoescape` with an explicit default is important: the default only
#: escapes files with known extensions, and these templates are strings with no
#: filename at all, so without `default_for_string=True` nothing would be
#: escaped.
_env = Environment(
    autoescape=select_autoescape(default_for_string=True, default=True),
    trim_blocks=True,
    lstrip_blocks=True,
)

#: Shared shell so every message looks like it came from the same place.
#: Deliberately plain: email clients support a 1998-era subset of CSS, so
#: inline styles and tables are the reliable route, and anything cleverer
#: renders as a mess in Outlook.
_LAYOUT = """\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#0e0b1e;font-family:Georgia,'Times New Roman',serif;color:#efe9ff;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0e0b1e;padding:32px 12px;">
      <tr><td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#171232;border:1px solid #3b2f6b;border-radius:14px;overflow:hidden;">
          <tr><td style="padding:28px 32px 12px;border-bottom:1px solid #3b2f6b;">
            <div style="font-size:22px;letter-spacing:0.16em;text-transform:uppercase;color:#f2c6ff;">{{ site_name }}</div>
          </td></tr>
          <tr><td style="padding:28px 32px;font-size:15px;line-height:1.65;">
{{ content }}
          </td></tr>
          <tr><td style="padding:18px 32px 28px;border-top:1px solid #3b2f6b;font-size:12px;color:#9d92c9;">
            This is an automated message about your account or order.
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>
"""


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    """A template rendered against a context.

    Attributes:
        subject: The subject line.
        html: HTML body.
        text: Plain-text alternative.
    """

    subject: str
    html: str
    text: str


@dataclass(frozen=True, slots=True)
class EmailTemplate:
    """A named template: subject, HTML fragment and text body.

    Attributes:
        subject: Jinja source for the subject line.
        html: Jinja source for the body, inserted into the shared layout.
        text: Jinja source for the plain-text alternative.
    """

    subject: str
    html: str
    text: str

    def render(self, context: dict[str, Any], *, site_name: str) -> RenderedEmail:
        """Render this template.

        Args:
            context: Template variables.
            site_name: Brand name for the layout header.

        Returns:
            The rendered subject and both bodies.
        """
        content = _env.from_string(self.html).render(**context)
        return RenderedEmail(
            subject=_env.from_string(self.subject).render(**context).strip(),
            html=_env.from_string(_LAYOUT).render(content=content, site_name=site_name),
            text=_env.from_string(self.text).render(**context).strip(),
        )


TEMPLATES: dict[str, EmailTemplate] = {
    "password_reset": EmailTemplate(
        subject="Reset your password",
        html="""
<p>Hello {{ fullName }},</p>
<p>We received a request to reset your password. This link is valid for
{{ expiresMinutes }} minutes and can be used once.</p>
<p style="margin:26px 0;">
  <a href="{{ resetUrl }}" style="display:inline-block;padding:12px 26px;background:#f2c6ff;color:#17102e;text-decoration:none;border-radius:8px;font-weight:bold;letter-spacing:0.05em;">Reset password</a>
</p>
<p style="color:#9d92c9;font-size:13px;">If you did not ask for this, you can
safely ignore this message. Your password will not change.</p>
""",
        text="""\
Hello {{ fullName }},

We received a request to reset your password. The link below is valid for
{{ expiresMinutes }} minutes and can be used once.

{{ resetUrl }}

If you did not ask for this, ignore this message. Your password will not change.
""",
    ),
    "order_confirmation": EmailTemplate(
        subject="Order {{ orderNumber }} confirmed",
        html="""
<p>Thank you — your order is confirmed.</p>
<p style="font-size:18px;letter-spacing:0.1em;color:#f2c6ff;">{{ orderNumber }}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0;border-top:1px solid #3b2f6b;">
{% for item in items %}
  <tr>
    <td style="padding:10px 0;border-bottom:1px solid #2a2150;">
      {{ item.title }}{% if item.variant %} — {{ item.variant }}{% endif %}
      <span style="color:#9d92c9;">&times; {{ item.quantity }}</span>
    </td>
    <td align="right" style="padding:10px 0;border-bottom:1px solid #2a2150;">
      {{ (item.totalCents / 100) | round(2) }} {{ currency | upper }}
    </td>
  </tr>
{% endfor %}
  <tr>
    <td style="padding:14px 0;font-weight:bold;">Total</td>
    <td align="right" style="padding:14px 0;font-weight:bold;color:#f2c6ff;">
      {{ (totalCents / 100) | round(2) }} {{ currency | upper }}
    </td>
  </tr>
</table>
<p style="color:#9d92c9;font-size:13px;">We will email you again when it ships.</p>
""",
        text="""\
Thank you - your order is confirmed.

Order {{ orderNumber }}

{% for item in items %}
- {{ item.title }}{% if item.variant %} ({{ item.variant }}){% endif %} x{{ item.quantity }}: {{ (item.totalCents / 100) | round(2) }} {{ currency | upper }}
{% endfor %}

Total: {{ (totalCents / 100) | round(2) }} {{ currency | upper }}

We will email you again when it ships.
""",
    ),
    "order_shipped": EmailTemplate(
        subject="Order {{ orderNumber }} is on its way",
        html="""
<p>Your order has shipped.</p>
<p style="font-size:18px;letter-spacing:0.1em;color:#f2c6ff;">{{ orderNumber }}</p>
{% if trackingUrl %}
<p style="margin:26px 0;">
  <a href="{{ trackingUrl }}" style="display:inline-block;padding:12px 26px;background:#f2c6ff;color:#17102e;text-decoration:none;border-radius:8px;font-weight:bold;">Track your parcel</a>
</p>
{% endif %}
""",
        text="""\
Your order has shipped.

Order {{ orderNumber }}
{% if trackingUrl %}
Track it here: {{ trackingUrl }}
{% endif %}
""",
    ),
    "email_verification": EmailTemplate(
        subject="Confirm your email address",
        html="""
<p>Hello {{ fullName }},</p>
<p>Confirm this address so we can send you receipts and order updates.</p>
<p style="margin:26px 0;">
  <a href="{{ verifyUrl }}" style="display:inline-block;padding:12px 26px;background:#f2c6ff;color:#17102e;text-decoration:none;border-radius:8px;font-weight:bold;letter-spacing:0.05em;">Confirm email</a>
</p>
<p style="color:#9d92c9;font-size:13px;">This link is valid for {{ expiresDays }} days.
If you did not create an account, you can ignore this message.</p>
""",
        text="""\
Hello {{ fullName }},

Confirm this address so we can send you receipts and order updates:

{{ verifyUrl }}

This link is valid for {{ expiresDays }} days. If you did not create an account,
ignore this message.
""",
    ),
    "welcome": EmailTemplate(
        subject="Welcome to {{ siteName }}",
        html="""
<p>Hello {{ fullName }},</p>
<p>Your account is ready. You can now check out faster and follow your orders
from one place.</p>
""",
        text="""\
Hello {{ fullName }},

Your account is ready. You can now check out faster and follow your orders from
one place.
""",
    ),
}


def render_template(name: str, context: dict[str, Any], *, site_name: str) -> RenderedEmail:
    """Render a registered template by name.

    Args:
        name: Template key, e.g. ``"order_confirmation"``.
        context: Template variables.
        site_name: Brand name for the layout header.

    Returns:
        The rendered email.

    Raises:
        ValidationFailedError: If no such template exists. The caller is
            another one of our services, so naming the available templates in
            the error is a help, not a disclosure.
    """
    template = TEMPLATES.get(name)
    if template is None:
        raise ValidationFailedError(
            f"Unknown email template: {name}",
            details={"available": sorted(TEMPLATES)},
        )
    return template.render(context, site_name=site_name)
