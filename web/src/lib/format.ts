/**
 * Display formatting.
 *
 * All money in this app is an integer number of minor units. Division by 100
 * happens here, once, at the moment of display — never in a calculation. See
 * ecom_shared/schemas.py for why.
 */

/**
 * Format an integer minor-unit amount as currency.
 *
 * @param cents - Amount in minor units, e.g. 1234.
 * @param currency - ISO 4217 code, any case.
 * @returns A localised string, e.g. "$12.34".
 */
export function formatMoney(cents: number, currency = "usd"): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency.toUpperCase(),
      minimumFractionDigits: 2,
    }).format(cents / 100);
  } catch {
    // An unknown currency code makes Intl throw. Showing the number is better
    // than showing an error boundary.
    return `${(cents / 100).toFixed(2)} ${currency.toUpperCase()}`;
  }
}

/**
 * Format a count with thousands separators.
 *
 * @param value - The number to format.
 * @returns A localised string, e.g. "12,345".
 */
export function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value);
}

/**
 * Format an ISO timestamp as a readable date.
 *
 * Uses the visitor's own locale, so the same instant reads naturally whether
 * they are in Ohio or Osaka.
 *
 * @param iso - ISO 8601 timestamp.
 * @param withTime - Include the time of day.
 * @returns A localised date string, or "—" if the input is empty or invalid.
 */
export function formatDate(iso: string | null | undefined, withTime = false): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";

  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(date);
}

/**
 * Format a timestamp as a relative age, e.g. "3 days ago".
 *
 * @param iso - ISO 8601 timestamp.
 * @returns A relative string, or "—" if the input is empty or invalid.
 */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";

  const seconds = (date.getTime() - Date.now()) / 1000;
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];

  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) {
      return formatter.format(Math.round(seconds / size), unit);
    }
  }
  return formatter.format(Math.round(seconds), "second");
}

/** Human-readable label for an order status. */
export const ORDER_STATUS_LABELS: Record<string, string> = {
  pending_payment: "Awaiting payment",
  paid: "Paid",
  fulfilled: "Shipped",
  delivered: "Delivered",
  cancelled: "Cancelled",
  refunded: "Refunded",
};

/** Tailwind classes for an order-status pill. */
export const ORDER_STATUS_TONE: Record<string, string> = {
  pending_payment: "border-warn/40 text-warn",
  paid: "border-ok/40 text-ok",
  fulfilled: "border-cyan/40 text-cyan",
  delivered: "border-ok/40 text-ok",
  cancelled: "border-faint/40 text-faint",
  refunded: "border-danger/40 text-danger",
};
