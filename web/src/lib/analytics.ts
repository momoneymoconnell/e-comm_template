/**
 * Client-side event reporting.
 *
 * Sends the minimum the dashboard needs and nothing more. Everything
 * identifying — the IP, the user agent, the signed-in user — is derived
 * server-side, because a client-supplied value for any of them is trivially
 * forged and would corrupt the data.
 *
 * Failures are swallowed. Analytics must never break a page: a shopper cannot
 * buy anything from an error boundary.
 */

import { apiBaseUrl } from "./api";

/** Event names the API accepts. Anything else is discarded server-side. */
export type EventType =
  | "page_view"
  | "product_view"
  | "add_to_cart"
  | "remove_from_cart"
  | "checkout_started"
  | "checkout_completed"
  | "search"
  | "signup"
  | "login";

const SESSION_KEY = "ecom_session_id";

/**
 * Get or create this tab's session identifier.
 *
 * Stored in `sessionStorage`, not `localStorage`, so it lasts one browsing
 * session and is gone when the tab closes. That is what makes it a *session*
 * identifier rather than a persistent tracking cookie, and it is why this
 * needs no consent banner.
 *
 * @returns An opaque session identifier, or null if storage is unavailable.
 */
function sessionId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    let id = window.sessionStorage.getItem(SESSION_KEY);
    if (!id) {
      id = crypto.randomUUID();
      window.sessionStorage.setItem(SESSION_KEY, id);
    }
    return id;
  } catch {
    // Private mode, or storage disabled entirely. Events still send; they
    // simply cannot be grouped into a session.
    return null;
  }
}

/**
 * Report an event.
 *
 * Uses `sendBeacon` when available. A normal `fetch` started during a page
 * transition is cancelled when the page unloads, which loses precisely the
 * events that mark someone leaving — the ones a funnel most needs.
 *
 * @param eventType - What happened.
 * @param properties - Event-specific context. Keep it small and non-personal.
 */
export function track(eventType: EventType, properties: Record<string, unknown> = {}): void {
  if (typeof window === "undefined") return;

  const payload = JSON.stringify({
    eventType,
    sessionId: sessionId(),
    path: window.location.pathname,
    referrer: document.referrer || null,
    properties,
  });

  const url = `${apiBaseUrl()}/api/analytics/events`;

  try {
    if (navigator.sendBeacon) {
      navigator.sendBeacon(url, new Blob([payload], { type: "application/json" }));
      return;
    }
    void fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
      credentials: "include",
      keepalive: true,
    }).catch(() => {
      /* Analytics must never surface an error to the shopper. */
    });
  } catch {
    /* Same. */
  }
}
