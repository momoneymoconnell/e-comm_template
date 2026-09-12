"""Base Pydantic models and the pagination envelope.

Two conventions enforced here, applied across every service:

* **camelCase on the wire, snake_case in Python.** The API speaks the frontend's
  dialect so no TypeScript code has to translate, while Python stays PEP 8.
  `alias_generator` does this automatically — you never write the alias by hand.
* **Money is always an integer of minor units.** ``total_cents: int``, never
  ``total: float``. Binary floats cannot represent 0.10 exactly, so float money
  accumulates rounding error and eventually a customer is charged a cent more
  than the page showed. Stripe's API works in minor units for the same reason;
  formatting to "$12.34" happens once, in the UI.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """Base class for every request and response model.

    Configuration choices and why:

    * ``alias_generator=to_camel`` — ``total_cents`` is exposed as ``totalCents``.
    * ``populate_by_name=True`` — both spellings are accepted on input, so
      Python-side construction in tests stays natural.
    * ``extra="forbid"`` — an unexpected field is a 422, not a silent no-op.
      This catches frontend typos immediately, and stops mass-assignment: a
      client cannot smuggle ``{"role": "admin"}`` into a profile update and
      hope something downstream reads it.
    * ``str_strip_whitespace=True`` — trailing spaces from copy-paste never
      create a "duplicate" email that is not actually a duplicate.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
        from_attributes=True,  # allows Model.model_validate(orm_row)
    )


class PageParams(ApiModel):
    """Query parameters for a paginated list endpoint.

    Offset pagination is used because admin tables need to jump to page 7, which
    cursor pagination cannot do. The `limit` ceiling is the important part: it
    stops ``?limit=1000000`` from becoming a one-request denial of service.
    """

    page: int = Field(default=1, ge=1, description="1-based page number.")
    page_size: int = Field(
        default=25, ge=1, le=100, description="Items per page. Hard maximum of 100."
    )

    @property
    def offset(self) -> int:
        """SQL ``OFFSET`` for this page."""
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """SQL ``LIMIT`` for this page."""
        return self.page_size


class Page[T](ApiModel):
    """A paginated slice of results plus the metadata needed to render a pager.

    ``total`` is included so the UI can show "Page 3 of 12" and disable the next
    button on the last page. It costs a second ``COUNT(*)`` query, which is a
    fair price at admin-table scale.
    """

    items: list[T] = Field(description="The results for this page.")
    total: int = Field(description="Total matching items across all pages.")
    page: int = Field(description="Current 1-based page number.")
    page_size: int = Field(description="Items per page.")

    @property
    def total_pages(self) -> int:
        """Number of pages available, at least 1 even when empty."""
        if self.page_size == 0:
            return 1
        return max(1, -(-self.total // self.page_size))  # ceiling division

    @classmethod
    def build(cls, items: list[T], total: int, params: PageParams) -> Page[T]:
        """Assemble a page from a query result.

        Args:
            items: The rows for this page.
            total: Total count matching the filter.
            params: The pagination parameters that produced `items`.

        Returns:
            The populated page envelope.
        """
        return cls(items=items, total=total, page=params.page, page_size=params.page_size)


class HealthStatus(ApiModel):
    """Response body for ``/health``.

    Attributes:
        status: ``"ok"`` or ``"degraded"``.
        service: Which service answered — invaluable when a proxy rule is wrong
            and you are unknowingly talking to the wrong container.
        version: Deployed version string.
        database: Whether Postgres is reachable.
        checked_at: Server time, which also reveals clock skew between hosts.
    """

    status: str
    service: str
    version: str
    database: bool
    checked_at: datetime


class Message(ApiModel):
    """Trivial ``{"message": "..."}`` body for endpoints with nothing to return.

    Preferred over ``204 No Content`` because a consistent JSON body means the
    frontend's response parser has no special cases.
    """

    message: str
