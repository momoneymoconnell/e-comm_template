/**
 * The single way this app talks to the API.
 *
 * Three things are handled here so no component has to think about them:
 *
 * 1. **Which base URL.** In the browser the gateway is reached at its public
 *    address; during server rendering the same gateway is a container on the
 *    compose network, where "localhost" would mean the web container itself.
 * 2. **CSRF.** Unsafe methods must echo the `csrf_token` cookie in an
 *    `X-CSRF-Token` header, or the gateway rejects them with 403.
 * 3. **Silent token refresh.** Access tokens live 15 minutes. On a 401 the
 *    client refreshes once and retries, so a session that is merely stale
 *    never surfaces to the user as an error.
 *
 * Errors arrive in one envelope from every service, so `ApiError` below is the
 * only error shape any caller has to handle.
 */

import type { ApiErrorBody } from "./types";

/**
 * Base URL for API calls.
 *
 * Server-side rendering runs inside the compose network, where `localhost` is
 * the web container. It must reach the gateway by its service name instead —
 * getting this wrong produces a connection-refused that only ever happens in
 * Docker and never in `npm run dev`.
 */
export function apiBaseUrl(): string {
  if (typeof window === "undefined") {
    return process.env.INTERNAL_API_URL ?? "http://gateway:8000";
  }
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";
}

/**
 * Resolve a media path returned by the API into a URL the browser can load.
 *
 * The catalog service returns image paths relative to the API, like
 * `/api/catalog/media/abc.jpg`. The browser resolves a relative path against
 * the page it is on - the web origin - not against the API, so on any setup
 * where the two differ (which is every setup here, since the API is on its own
 * port) the image silently 404s.
 *
 * Absolute URLs are passed through untouched, so a product whose image lives
 * on a CDN still works.
 *
 * @param path - A media path or absolute URL from the API. May be null.
 * @returns An absolute URL, or null.
 */
export function mediaUrl(path: string | null | undefined): string | null {
  if (!path) return null;
  if (/^https?:\/\//i.test(path)) return path;

  // Always the PUBLIC base, never `apiBaseUrl()`.
  //
  // `apiBaseUrl()` returns the internal container address during server
  // rendering, which is correct for fetching but wrong here: this value ends
  // up in an `<img src>` that a browser has to load, and no browser can
  // resolve `http://gateway:8000`. Server-rendered product images came back
  // broken until this stopped following the fetch base.
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";
  return `${base}${path.startsWith("/") ? "" : "/"}${path}`;
}

/** An error carrying the API's structured body. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly requestId: string;

  constructor(status: number, body: ApiErrorBody | null, fallback: string) {
    const message = body?.error?.message ?? fallback;
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.error?.code ?? "unknown_error";
    this.details = body?.error?.details ?? {};
    this.requestId = body?.error?.request_id ?? "";
  }

  /** Field-level validation messages, for rendering next to form inputs. */
  get fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [key, value] of Object.entries(this.details)) {
      if (typeof value === "string") out[key] = value;
    }
    return out;
  }
}

/** Read a cookie by name. Returns null on the server, where there is none. */
function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(
    new RegExp("(?:^|; )" + name.replace(/([.$?*|{}()[\]\\/+^])/g, "\\$1") + "=([^;]*)"),
  );
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

interface RequestOptions extends Omit<RequestInit, "body"> {
  /** JSON body. Serialised and given the right content type automatically. */
  json?: unknown;
  /** Set false to skip the refresh-and-retry on 401. */
  retryOnUnauthorized?: boolean;
}

/**
 * Perform an API request.
 *
 * @param path - Path beneath `/api`, e.g. `"/catalog/products"`.
 * @param options - Fetch options, plus `json` for a JSON body.
 * @returns The parsed response body.
 * @throws {ApiError} On any non-2xx response.
 */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { json, retryOnUnauthorized = true, ...init } = options;
  const method = (init.method ?? "GET").toUpperCase();

  const headers = new Headers(init.headers);
  if (json !== undefined) {
    headers.set("Content-Type", "application/json");
  }

  // The double-submit token. Without it the gateway returns 403 on any
  // cookie-authenticated state change.
  if (!SAFE_METHODS.has(method)) {
    const csrf = readCookie("csrf_token");
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }

  const response = await fetch(`${apiBaseUrl()}/api${path}`, {
    ...init,
    method,
    headers,
    body: json !== undefined ? JSON.stringify(json) : (init as RequestInit).body,
    // Required for the session cookies to be sent and stored at all.
    credentials: "include",
    // Never serve an API response from cache: a cached cart or order list is
    // worse than a slow one.
    cache: "no-store",
  });

  // A 401 usually means the 15-minute access token simply expired. Refresh
  // once and retry, so an ordinary stale session is invisible to the user.
  // `retryOnUnauthorized: false` on the retry itself prevents a loop when the
  // refresh token is genuinely dead.
  if (response.status === 401 && retryOnUnauthorized && typeof window !== "undefined") {
    const refreshed = await tryRefresh();
    if (refreshed) {
      return apiFetch<T>(path, { ...options, retryOnUnauthorized: false });
    }
  }

  if (!response.ok) {
    let body: ApiErrorBody | null = null;
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      // A proxy error page or an empty body. The status alone is enough.
    }
    throw new ApiError(response.status, body, `Request failed (${response.status}).`);
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** Module-level promise so concurrent 401s trigger exactly one refresh. */
let refreshInFlight: Promise<boolean> | null = null;

/**
 * Exchange the refresh cookie for a new session.
 *
 * Deduplicated on purpose. A page that fires five requests at once would
 * otherwise trigger five refreshes; because refresh tokens rotate and a
 * consumed token is treated as a theft signal, that would revoke every session
 * the user has and sign them out — a self-inflicted denial of service.
 *
 * @returns Whether the session was renewed.
 */
async function tryRefresh(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    try {
      const csrf = readCookie("csrf_token");
      const response = await fetch(`${apiBaseUrl()}/api/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: csrf ? { "X-CSRF-Token": csrf } : undefined,
        cache: "no-store",
      });
      return response.ok;
    } catch {
      return false;
    } finally {
      // Cleared on the next tick so callers awaiting this promise all observe
      // the same result before a new refresh can begin.
      setTimeout(() => {
        refreshInFlight = null;
      }, 0);
    }
  })();

  return refreshInFlight;
}

/**
 * Fetch during server rendering, forwarding the browser's cookies.
 *
 * A server component has no ambient cookie jar: it must pass the incoming
 * request's `Cookie` header through explicitly, or every server-rendered call
 * is anonymous and the page flashes a signed-out state before hydrating.
 *
 * @param path - Path beneath `/api`.
 * @param cookieHeader - The `Cookie` header from the incoming request.
 * @returns The parsed body, or null if the request failed.
 */
export async function serverFetch<T>(
  path: string,
  cookieHeader?: string,
): Promise<T | null> {
  try {
    const response = await fetch(`${apiBaseUrl()}/api${path}`, {
      headers: cookieHeader ? { Cookie: cookieHeader } : undefined,
      cache: "no-store",
    });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    // A server-rendered page must still render when a service is down. The
    // caller shows an empty state rather than a 500 for the whole page.
    return null;
  }
}
