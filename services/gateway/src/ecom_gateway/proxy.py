"""The reverse proxy itself.

Everything the browser sends arrives here. The gateway's job is to decide
whether a request is allowed through, and if so, to forward it to exactly one
of six known services and stream the answer back.

What it deliberately does **not** do: authenticate. Each service verifies the
access token itself — see `ecom_shared.identity` for why trusting a
gateway-supplied identity header is a bad trade. The gateway forwards the
token; the service decides.
"""

from __future__ import annotations

import httpx
from ecom_shared.errors import AppError, NotFoundError, UpstreamError
from ecom_shared.logging import get_logger, get_request_id
from ecom_shared.middleware import REQUEST_ID_HEADER
from fastapi import Request
from starlette.background import BackgroundTask
from starlette.responses import Response, StreamingResponse

from ecom_gateway.config import GatewaySettings

log = get_logger(__name__)

#: Hop-by-hop headers, which describe one TCP connection rather than the
#: message. Forwarding them corrupts the next hop — passing on
#: `Content-Length` from a body we may have altered, or `Connection: close`,
#: produces failures that are extremely annoying to diagnose. Defined by
#: RFC 9110 §7.6.1.
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "content-encoding",
        "host",
    }
)

#: Timeout for proxied calls. The read timeout is generous enough for a report
#: query, while the connect timeout stays short so an unreachable service fails
#: quickly rather than tying up a worker.
PROXY_TIMEOUT = httpx.Timeout(30.0, connect=3.0)


def resolve_target(settings: GatewaySettings, path: str) -> str:
    """Map a public ``/api/...`` path to an internal service URL.

    ``/api/catalog/products?page=2`` becomes
    ``http://catalog:8000/catalog/products``.

    Two refusals are the security-relevant part:

    * An unknown first segment is a 404. The route table is a closed mapping,
      so a crafted prefix cannot make the gateway connect to an arbitrary host.
    * Any path containing ``/internal/`` is a 404, no matter which service it
      names. Internal endpoints create payment intents, move inventory and mark
      orders paid. They are additionally protected by service tokens, but the
      gateway simply refusing to route to them means a mistake in one layer is
      not enough on its own.

    Args:
        settings: Supplies the route table.
        path: The public request path.

    Returns:
        The absolute URL to forward to.

    Raises:
        NotFoundError: For an unknown service or an internal path.
    """
    trimmed = path.removeprefix("/api/").strip("/")
    if not trimmed:
        raise NotFoundError("Not found.")

    segments = trimmed.split("/")
    service = segments[0]

    if "internal" in segments:
        log.warning("gateway_internal_path_refused", path=path)
        raise NotFoundError("Not found.")

    base = settings.routes.get(service)
    if base is None:
        raise NotFoundError("Not found.")

    # The service's own routers are mounted under the same name, so the public
    # path maps across unchanged: /api/catalog/products -> /catalog/products.
    return f"{base}/{trimmed}"


def filter_headers(headers: httpx.Headers) -> list[tuple[str, str]]:
    """Strip hop-by-hop headers, preserving repeated ones.

    Returns a **list of pairs**, not a dict, and this is load-bearing.

    A login response carries three separate `Set-Cookie` headers — access,
    refresh and CSRF. Collapsing them into a dict keeps one and discards the
    rest; httpx's `.items()` is worse still, joining them into a single
    comma-separated value. That is not valid for `Set-Cookie` (cookie values
    and `Expires` dates legitimately contain commas), so the client parses one
    malformed cookie and drops all three.

    The failure is quiet and confusing: login returns 200, the response looks
    correct, and the browser is still anonymous on the next request.
    `multi_items()` keeps each occurrence separate.

    Args:
        headers: The headers to filter.

    Returns:
        ``(name, value)`` pairs safe to pass to the next hop, repeats intact.
    """
    return [
        (key, value)
        for key, value in headers.multi_items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    ]


async def forward(
    request: Request, client: httpx.AsyncClient, settings: GatewaySettings
) -> Response:
    """Forward a request to its service and stream the response back.

    The body is streamed rather than buffered. Reading it into memory first
    would cap upload size at available RAM and, more importantly here, would
    risk altering the exact bytes — which would break Stripe webhook signature
    verification, since the signature covers the literal payload.

    Args:
        request: The incoming request.
        client: A shared httpx client, so connections are pooled across
            requests instead of a TCP and TLS handshake per call.
        settings: Supplies the route table.

    Returns:
        The service's response, streamed.

    Raises:
        NotFoundError: If the path does not resolve to a known service.
        UpstreamError: If the service is unreachable or times out.
    """
    target = resolve_target(settings, request.url.path)

    # `request.headers` is Starlette's, so it is converted to httpx's type
    # first to get `multi_items()`. Repeated request headers matter less than
    # repeated response headers, but a request can carry several `Cookie` or
    # `Accept` headers and dropping them would be just as wrong.
    headers = filter_headers(httpx.Headers(request.headers.raw))

    # Correlation: the same ID appears on the gateway's log line and on every
    # downstream service's, so one customer action is traceable end to end.
    headers.append((REQUEST_ID_HEADER, get_request_id()))

    # Tell the service who the original client was. The service only trusts
    # this because nothing but the gateway can reach it on the compose network.
    if request.client:
        headers.append(("X-Forwarded-For", request.client.host))
    headers.append(("X-Forwarded-Proto", request.url.scheme))

    try:
        upstream = client.build_request(
            method=request.method,
            url=target,
            headers=headers,
            params=dict(request.query_params),
            content=request.stream(),
        )
        response = await client.send(upstream, stream=True)
    except httpx.TimeoutException as exc:
        log.error("gateway_upstream_timeout", target=target)
        raise UpstreamError("That request took too long. Please try again.") from exc
    except httpx.HTTPError as exc:
        log.error("gateway_upstream_unreachable", target=target, error=str(exc))
        raise UpstreamError("That service is temporarily unavailable.") from exc

    return _streaming_response(response)


def _streaming_response(response: httpx.Response) -> Response:
    """Wrap an upstream response as a streaming Starlette response.

    The upstream response must be closed once the body has been relayed, or the
    connection is never returned to the pool and the gateway slowly runs out of
    connections. `BackgroundTask` runs after the response is fully sent, which
    is exactly the right moment.

    Args:
        response: The open, streaming upstream response.

    Returns:
        A response that relays the upstream body and then closes it.
    """
    # `aiter_raw`, not `aiter_bytes`: the body is relayed exactly as received,
    # without decompressing. Decoding it here while still forwarding the
    # upstream Content-Encoding header would leave the browser trying to gunzip
    # plain text.
    relayed = StreamingResponse(
        response.aiter_raw(),
        status_code=response.status_code,
        background=BackgroundTask(response.aclose),
    )

    # Assigned after construction rather than passed in: Starlette's `headers`
    # argument takes a mapping, which would collapse the repeated `Set-Cookie`
    # headers this whole function exists to preserve. `raw_headers` is the list
    # of byte pairs that goes on the wire verbatim.
    relayed.raw_headers = [
        (key.encode("latin-1"), value.encode("latin-1"))
        for key, value in filter_headers(response.headers)
    ]
    return relayed


__all__ = ["AppError", "filter_headers", "forward", "resolve_target"]
