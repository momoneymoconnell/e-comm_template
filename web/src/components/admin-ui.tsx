"use client";

/**
 * Presentational pieces used across the admin console.
 */

import type { ReactNode } from "react";

import { Panel, Skeleton } from "@/components/ui";

/**
 * A single headline metric.
 */
export function Stat({
  label,
  value,
  hint,
  tone = "default",
  loading = false,
}: {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "default" | "good" | "warn" | "bad";
  loading?: boolean;
}) {
  const toneClass = {
    default: "text-marble",
    good: "text-ok",
    warn: "text-warn",
    bad: "text-danger",
  }[tone];

  return (
    <Panel className="p-5">
      <p className="inscription text-[0.58rem] text-cyan/70">{label}</p>
      {loading ? (
        <Skeleton className="mt-3 h-7 w-24" />
      ) : (
        <p className={`mt-2 text-2xl ${toneClass}`}>{value}</p>
      )}
      {hint ? <p className="mt-1 text-xs text-faint">{hint}</p> : null}
    </Panel>
  );
}

/**
 * A scrollable table container.
 *
 * The `overflow-x-auto` is essential: an admin table has too many columns for a
 * phone, and without it the whole page scrolls sideways instead of the table.
 */
export function TableWrap({ children }: { children: ReactNode }) {
  return (
    <Panel className="overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[40rem] border-collapse text-sm">{children}</table>
      </div>
    </Panel>
  );
}

export function Th({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <th
      scope="col"
      className={`inscription border-b border-edge px-4 py-3 text-left text-[0.58rem] text-cyan/70 ${className}`}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <td className={`border-b border-edge/60 px-4 py-3 text-ink ${className}`}>{children}</td>
  );
}

/**
 * A compact bar chart, drawn as inline SVG.
 *
 * No charting library: a dependency that ships hundreds of kilobytes to draw
 * rectangles is not a good trade, and inline SVG scales, themes and prints
 * correctly for free.
 */
export function BarChart({
  data,
  label,
  valueFormatter = (n: number) => String(n),
}: {
  data: { label: string; value: number }[];
  label: string;
  valueFormatter?: (value: number) => string;
}) {
  if (data.length === 0) {
    return <p className="py-8 text-center text-sm text-faint">No data yet.</p>;
  }

  // `|| 1` guards against every value being zero, which would otherwise make
  // every bar `NaN%` tall and render nothing at all.
  const max = Math.max(...data.map((d) => d.value)) || 1;

  return (
    <figure>
      <figcaption className="sr-only">{label}</figcaption>
      {/* `justify-end` plus a bar max-width keeps a sparse series looking like
          a chart. Without the cap, a single data point stretched by flex-1
          fills the whole panel as one solid block. */}
      <div
        className="flex h-40 items-end justify-end gap-[2px]"
        role="img"
        aria-label={label}
      >
        {data.map((point, index) => (
          <div
            key={index}
            className="group relative min-w-[3px] flex-1"
            style={{ height: "100%", maxWidth: "24px" }}
            title={`${point.label}: ${valueFormatter(point.value)}`}
          >
            <div
              className="absolute bottom-0 w-full rounded-t-sm bg-gradient-to-t from-neon-dim to-cyan transition-opacity group-hover:opacity-80"
              style={{ height: `${Math.max(2, (point.value / max) * 100)}%` }}
            />
          </div>
        ))}
      </div>
      {/* A plain-text equivalent, so the chart is not invisible to a screen
          reader or when styles fail to load. */}
      <details className="mt-3">
        <summary className="cursor-pointer text-xs text-faint hover:text-cyan">
          View as data
        </summary>
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
          {data.map((point, index) => (
            <div key={index} className="flex justify-between gap-2">
              <dt className="text-faint">{point.label}</dt>
              <dd className="text-ink">{valueFormatter(point.value)}</dd>
            </div>
          ))}
        </dl>
      </details>
    </figure>
  );
}

/**
 * A horizontal funnel, one row per step.
 */
export function FunnelChart({
  steps,
}: {
  steps: { step: string; sessions: number; rateFromTop: number }[];
}) {
  const labels: Record<string, string> = {
    page_view: "Visited",
    product_view: "Viewed a product",
    add_to_cart: "Added to cart",
    checkout_started: "Started checkout",
    checkout_completed: "Completed",
  };

  const top = steps[0]?.sessions ?? 0;
  if (top === 0) {
    return <p className="py-8 text-center text-sm text-faint">No traffic recorded yet.</p>;
  }

  return (
    <ol className="space-y-3">
      {steps.map((step) => (
        <li key={step.step}>
          <div className="mb-1.5 flex items-baseline justify-between text-xs">
            <span className="text-muted">{labels[step.step] ?? step.step}</span>
            <span className="text-ink">
              {step.sessions}{" "}
              <span className="text-faint">({step.rateFromTop.toFixed(1)}%)</span>
            </span>
          </div>
          <div className="h-2.5 overflow-hidden rounded-full bg-raised">
            <div
              className="h-full rounded-full bg-gradient-to-r from-cyan to-neon"
              style={{ width: `${Math.max(1, (step.sessions / top) * 100)}%` }}
            />
          </div>
        </li>
      ))}
    </ol>
  );
}
