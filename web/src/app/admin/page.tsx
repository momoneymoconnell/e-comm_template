"use client";

/**
 * Admin overview: revenue, traffic, catalogue and account health at a glance.
 *
 * Every panel is fetched independently and fails independently. One service
 * being down greys out its own card instead of blanking the dashboard — which
 * matters most precisely when something is wrong.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { BarChart, FunnelChart, Stat } from "@/components/admin-ui";
import { ErrorNote, Meander, Panel } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import { formatMoney, formatNumber } from "@/lib/format";
import type {
  CatalogStats,
  FunnelStep,
  OrderStats,
  PaymentStats,
  TrafficPoint,
  TrafficSummary,
  UserStats,
} from "@/lib/types";

interface Dashboard {
  users: UserStats | null;
  catalog: CatalogStats | null;
  orders: OrderStats | null;
  payments: PaymentStats | null;
  traffic: TrafficSummary | null;
  series: TrafficPoint[];
  funnel: FunnelStep[];
}

export default function AdminOverviewPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      /** Resolve to null instead of throwing, so one failure is contained. */
      const safe = async <T,>(path: string): Promise<T | null> => {
        try {
          return await apiFetch<T>(path);
        } catch {
          return null;
        }
      };

      try {
        // `Promise.all` over already-safe calls: they run concurrently, and
        // none can reject, so the dashboard always renders something.
        const [users, catalog, orders, payments, traffic, series, funnel] =
          await Promise.all([
            safe<UserStats>("/auth/admin/stats"),
            safe<CatalogStats>("/catalog/admin/stats"),
            safe<OrderStats>("/orders/admin/stats/summary"),
            safe<PaymentStats>("/payments/admin/stats"),
            safe<TrafficSummary>("/analytics/admin/summary"),
            safe<TrafficPoint[]>("/analytics/admin/timeseries?days=30"),
            safe<FunnelStep[]>("/analytics/admin/funnel?days=30"),
          ]);

        setData({
          users,
          catalog,
          orders,
          payments,
          traffic,
          series: series ?? [],
          funnel: funnel ?? [],
        });
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Could not load the dashboard.");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const orders = data?.orders;
  const payments = data?.payments;
  const traffic = data?.traffic;

  return (
    <div className="space-y-10">
      {error ? <ErrorNote>{error}</ErrorNote> : null}

      <section>
        <h2 className="inscription mb-4 text-[0.68rem] text-cyan/80">Last 30 days</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Revenue"
            value={orders ? formatMoney(orders.recentRevenueCents) : "—"}
            hint={orders ? `${orders.recentOrders} orders placed` : undefined}
            loading={loading}
            tone="good"
          />
          <Stat
            label="Average order"
            value={orders ? formatMoney(orders.averageOrderValueCents) : "—"}
            hint="All time"
            loading={loading}
          />
          <Stat
            label="Visitors"
            value={traffic ? formatNumber(traffic.uniqueVisitors) : "—"}
            hint={traffic ? `${formatNumber(traffic.pageViews)} page views` : undefined}
            loading={loading}
          />
          <Stat
            label="Today"
            value={traffic ? formatNumber(traffic.todayVisitors) : "—"}
            hint={traffic ? `${formatNumber(traffic.todayPageViews)} views today` : undefined}
            loading={loading}
          />
        </div>
      </section>

      <section className="grid gap-6 lg:grid-cols-2">
        <Panel className="p-6">
          <h2 className="inscription text-[0.68rem] text-cyan/80">Traffic, 30 days</h2>
          <Meander className="my-4" />
          {loading ? (
            <div className="skeleton h-40 rounded-md" />
          ) : (
            <BarChart
              label="Daily page views over the last 30 days"
              data={(data?.series ?? []).map((point) => ({
                label: point.day,
                value: point.pageViews,
              }))}
              valueFormatter={formatNumber}
            />
          )}
        </Panel>

        <Panel className="p-6">
          <h2 className="inscription text-[0.68rem] text-cyan/80">Checkout funnel</h2>
          <Meander className="my-4" />
          {loading ? (
            <div className="skeleton h-40 rounded-md" />
          ) : (
            <FunnelChart steps={data?.funnel ?? []} />
          )}
        </Panel>
      </section>

      <section>
        <h2 className="inscription mb-4 text-[0.68rem] text-cyan/80">All time</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Paid orders"
            value={orders ? formatNumber(orders.paidOrders) : "—"}
            hint={orders ? `${formatNumber(orders.totalOrders)} total` : undefined}
            loading={loading}
          />
          <Stat
            label="Captured"
            value={payments ? formatMoney(payments.capturedCents) : "—"}
            hint={payments ? `${payments.succeeded} successful payments` : undefined}
            loading={loading}
            tone="good"
          />
          <Stat
            label="Refunded"
            value={payments ? formatMoney(payments.refundedCents) : "—"}
            loading={loading}
            tone={payments && payments.refundedCents > 0 ? "warn" : "default"}
          />
          <Stat
            label="Failed payments"
            value={payments ? formatNumber(payments.failed) : "—"}
            loading={loading}
            tone={payments && payments.failed > 0 ? "bad" : "default"}
          />
        </div>
      </section>

      <section>
        <h2 className="inscription mb-4 text-[0.68rem] text-cyan/80">Catalogue and accounts</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Active products"
            value={data?.catalog ? formatNumber(data.catalog.activeProducts) : "—"}
            hint={data?.catalog ? `${data.catalog.draftProducts} drafts` : undefined}
            loading={loading}
          />
          <Stat
            label="Out of stock"
            value={data?.catalog ? formatNumber(data.catalog.outOfStockVariants) : "—"}
            loading={loading}
            tone={
              data?.catalog && data.catalog.outOfStockVariants > 0 ? "warn" : "default"
            }
          />
          <Stat
            label="Customers"
            value={data?.users ? formatNumber(data.users.total) : "—"}
            hint={data?.users ? `${data.users.newThisWeek} new this week` : undefined}
            loading={loading}
          />
          <Stat
            label="Administrators"
            value={data?.users ? formatNumber(data.users.admins) : "—"}
            loading={loading}
          />
        </div>
      </section>

      {orders && Object.keys(orders.byStatus).length > 0 ? (
        <section>
          <h2 className="inscription mb-4 text-[0.68rem] text-cyan/80">Orders by status</h2>
          <Panel className="flex flex-wrap gap-x-8 gap-y-3 p-6">
            {Object.entries(orders.byStatus).map(([status, count]) => (
              <div key={status}>
                <p className="text-lg text-marble">{formatNumber(count)}</p>
                <p className="text-xs capitalize text-faint">{status.replace(/_/g, " ")}</p>
              </div>
            ))}
          </Panel>
        </section>
      ) : null}

      <p className="text-xs text-faint">
        Revenue and product figures come from the dbt marts. Run{" "}
        <code className="rounded bg-void px-1.5 py-0.5 font-mono text-cyan">make dbt-build</code>{" "}
        to refresh them, or see{" "}
        <Link href="/admin/traffic" className="text-cyan underline-offset-4 hover:underline">
          traffic
        </Link>{" "}
        for live figures straight from Postgres.
      </p>
    </div>
  );
}
