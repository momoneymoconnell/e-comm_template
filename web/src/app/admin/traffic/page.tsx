"use client";

/**
 * Traffic analytics.
 *
 * Everything here is derived from pseudonymised events: no raw IP addresses,
 * no full user agents, no query strings. "Unique visitors" counts a hash whose
 * salt rotates daily, so it counts people within a day and visitor-days when
 * summed across a longer window — stated on the page so the number is not
 * quietly over-interpreted.
 */

import { useState } from "react";

import { BarChart, FunnelChart, Stat, TableWrap, Td, Th } from "@/components/admin-ui";
import { EmptyState, ErrorNote, Meander, Panel, Skeleton } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import { useAsyncData } from "@/lib/use-async";
import { formatNumber } from "@/lib/format";
import type {
  BreakdownRow,
  FunnelStep,
  TopPage,
  TrafficPoint,
  TrafficSummary,
} from "@/lib/types";

const WINDOWS = [7, 30, 90] as const;

export default function AdminTrafficPage() {
  const [days, setDays] = useState<number>(30);

  const { data, error, loading } = useAsyncData<{
    summary: TrafficSummary;
    series: TrafficPoint[];
    pages: TopPage[];
    funnel: FunnelStep[];
    devices: BreakdownRow[];
    referrers: BreakdownRow[];
  }>(async () => {
    // Six independent queries, issued together. Sequentially this page would
    // take as long as the sum of all six round trips.
    const [summary, series, pages, funnel, devices, referrers] = await Promise.all([
      apiFetch<TrafficSummary>(`/analytics/admin/summary?days=${days}`),
      apiFetch<TrafficPoint[]>(`/analytics/admin/timeseries?days=${days}`),
      apiFetch<TopPage[]>(`/analytics/admin/top-pages?days=${days}`),
      apiFetch<FunnelStep[]>(`/analytics/admin/funnel?days=${days}`),
      apiFetch<BreakdownRow[]>(`/analytics/admin/breakdown/device?days=${days}`),
      apiFetch<BreakdownRow[]>(`/analytics/admin/breakdown/referrer?days=${days}`),
    ]);
    return { summary, series, pages, funnel, devices, referrers };
  }, [days]);

  const summary = data?.summary ?? null;
  const series = data?.series ?? [];
  const pages = data?.pages ?? [];
  const funnel = data?.funnel ?? [];
  const devices = data?.devices ?? [];
  const referrers = data?.referrers ?? [];

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center gap-2">
        <span className="inscription text-[0.58rem] text-cyan/70">Window</span>
        {WINDOWS.map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => setDays(value)}
            aria-pressed={days === value}
            className={`inscription rounded-full border px-3.5 py-1 text-[0.6rem] transition-colors ${
              days === value
                ? "border-neon bg-neon/10 text-neon"
                : "border-edge text-muted hover:border-cyan hover:text-cyan"
            }`}
          >
            {value} days
          </button>
        ))}
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Page views"
          value={summary ? formatNumber(summary.pageViews) : "—"}
          loading={loading}
        />
        <Stat
          label="Unique visitors"
          value={summary ? formatNumber(summary.uniqueVisitors) : "—"}
          hint="Visitor-days over a multi-day window"
          loading={loading}
        />
        <Stat
          label="Sessions"
          value={summary ? formatNumber(summary.sessions) : "—"}
          loading={loading}
        />
        <Stat
          label="Today"
          value={summary ? formatNumber(summary.todayVisitors) : "—"}
          hint={summary ? `${formatNumber(summary.todayPageViews)} views` : undefined}
          loading={loading}
        />
      </div>

      <Panel className="p-6">
        <h2 className="inscription text-[0.66rem] text-cyan/80">Page views per day</h2>
        <Meander className="my-4" />
        {loading ? (
          <Skeleton className="h-40" />
        ) : (
          <BarChart
            label={`Page views per day over the last ${days} days`}
            data={series.map((point) => ({ label: point.day, value: point.pageViews }))}
            valueFormatter={formatNumber}
          />
        )}
      </Panel>

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel className="p-6">
          <h2 className="inscription text-[0.66rem] text-cyan/80">Checkout funnel</h2>
          <Meander className="my-4" />
          {loading ? <Skeleton className="h-40" /> : <FunnelChart steps={funnel} />}
        </Panel>

        <Panel className="p-6">
          <h2 className="inscription text-[0.66rem] text-cyan/80">Devices</h2>
          <Meander className="my-4" />
          {loading ? (
            <Skeleton className="h-40" />
          ) : devices.length > 0 ? (
            <dl className="space-y-2.5 text-sm">
              {devices.map((row) => (
                <div key={row.value} className="flex justify-between">
                  <dt className="capitalize text-muted">{row.value}</dt>
                  <dd className="text-ink">{formatNumber(row.sessions)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="py-6 text-center text-sm text-faint">No data yet.</p>
          )}
        </Panel>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <div>
          <h2 className="inscription mb-4 text-[0.66rem] text-cyan/80">Top pages</h2>
          {loading ? (
            <Skeleton className="h-40" />
          ) : pages.length > 0 ? (
            <TableWrap>
              <thead>
                <tr>
                  <Th>Path</Th>
                  <Th className="text-right">Views</Th>
                  <Th className="text-right">Visitors</Th>
                </tr>
              </thead>
              <tbody>
                {pages.map((row) => (
                  <tr key={row.path}>
                    <Td className="font-mono text-xs">{row.path}</Td>
                    <Td className="text-right text-xs">{formatNumber(row.views)}</Td>
                    <Td className="text-right text-xs">{formatNumber(row.visitors)}</Td>
                  </tr>
                ))}
              </tbody>
            </TableWrap>
          ) : (
            <EmptyState title="No page views yet">
              Browse the storefront to generate some.
            </EmptyState>
          )}
        </div>

        <div>
          <h2 className="inscription mb-4 text-[0.66rem] text-cyan/80">Referrers</h2>
          {loading ? (
            <Skeleton className="h-40" />
          ) : referrers.length > 0 ? (
            <TableWrap>
              <thead>
                <tr>
                  <Th>Source</Th>
                  <Th className="text-right">Sessions</Th>
                </tr>
              </thead>
              <tbody>
                {referrers.map((row) => (
                  <tr key={row.value}>
                    <Td className="text-xs">{row.value}</Td>
                    <Td className="text-right text-xs">{formatNumber(row.sessions)}</Td>
                  </tr>
                ))}
              </tbody>
            </TableWrap>
          ) : (
            <EmptyState title="No referrers recorded">
              Only the referring host is stored, never the full URL.
            </EmptyState>
          )}
        </div>
      </div>

      <p className="text-xs text-faint">
        Visitors are identified by a salted hash whose salt rotates every day, so
        nobody can be followed across days. Do Not Track is honoured, and query
        strings are stripped before anything is stored.
      </p>
    </div>
  );
}
