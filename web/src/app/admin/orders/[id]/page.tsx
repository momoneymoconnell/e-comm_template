"use client";

/**
 * A single order, with the controls to advance its status.
 *
 * The status buttons offered are derived from the order's current state, so an
 * impossible transition is never presented. The API enforces the same state
 * machine regardless — this only keeps the UI from inviting a 409.
 */

import Link from "next/link";
import { use, useState } from "react";

import { errorMessage } from "@/components/session-provider";
import {
  Button,
  EmptyState,
  ErrorNote,
  Meander,
  Panel,
  Pill,
  Skeleton,
} from "@/components/ui";
import { apiFetch } from "@/lib/api";
import { useAsyncData } from "@/lib/use-async";
import {
  ORDER_STATUS_LABELS,
  ORDER_STATUS_TONE,
  formatDate,
  formatMoney,
} from "@/lib/format";
import type { Order, OrderStatus } from "@/lib/types";

/**
 * Legal next states, mirroring ALLOWED_TRANSITIONS in the orders service.
 *
 * Duplicated here deliberately, and it is a duplication worth having: the
 * server is authoritative, and this exists only so the UI does not offer a
 * button that is guaranteed to fail.
 */
const NEXT_STATUSES: Record<OrderStatus, OrderStatus[]> = {
  pending_payment: ["cancelled"],
  paid: ["fulfilled", "refunded", "cancelled"],
  fulfilled: ["delivered", "refunded"],
  delivered: ["refunded"],
  cancelled: [],
  refunded: [],
};

export default function AdminOrderPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [pending, setPending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const {
    data: order,
    error: loadError,
    loading,
    reload,
  } = useAsyncData<Order>(() => apiFetch<Order>(`/orders/admin/${id}`), [id]);

  const error = actionError ?? loadError;

  async function transition(status: OrderStatus) {
    setPending(true);
    setActionError(null);
    try {
      await apiFetch<Order>(`/orders/admin/${id}/status`, {
        method: "PATCH",
        json: { status, note: `Marked ${status.replace(/_/g, " ")} from the admin console.` },
      });
      // Refetch: a transition can have side effects beyond the status field
      // (cancelling releases stock, and a new history entry is written).
      reload();
    } catch (caught) {
      setActionError(errorMessage(caught));
    } finally {
      setPending(false);
    }
  }

  if (loading) return <Skeleton className="h-72" />;

  if (!order) {
    return (
      <EmptyState title="Order not found">
        {error ?? "No order with that identifier."}
      </EmptyState>
    );
  }

  const nextStatuses = NEXT_STATUSES[order.status] ?? [];

  return (
    <div className="space-y-6">
      <Link
        href="/admin/orders"
        className="text-xs text-faint underline-offset-4 transition-colors hover:text-cyan hover:underline"
      >
        ← All orders
      </Link>

      <div className="flex flex-wrap items-center gap-4">
        <h2 className="inscription text-lg text-marble">{order.orderNumber}</h2>
        <Pill className={ORDER_STATUS_TONE[order.status] ?? "border-edge text-muted"}>
          {ORDER_STATUS_LABELS[order.status] ?? order.status}
        </Pill>
        <span className="text-xs text-faint">{formatDate(order.createdAt, true)}</span>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {nextStatuses.length > 0 ? (
        <Panel className="p-5">
          <p className="inscription mb-3 text-[0.6rem] text-cyan/70">Advance this order</p>
          <div className="flex flex-wrap gap-2">
            {nextStatuses.map((status) => (
              <Button
                key={status}
                tone={status === "refunded" || status === "cancelled" ? "danger" : "primary"}
                disabled={pending}
                onClick={() => void transition(status)}
              >
                Mark {ORDER_STATUS_LABELS[status]?.toLowerCase() ?? status}
              </Button>
            ))}
          </div>
          {order.status === "pending_payment" ? (
            <p className="mt-3 text-xs text-faint">
              Cancelling an unpaid order returns its reserved stock to the catalogue.
            </p>
          ) : null}
        </Panel>
      ) : (
        <p className="text-xs text-faint">
          This order is in a final state and cannot be advanced further.
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        <Panel className="p-6">
          <p className="inscription text-[0.64rem] text-cyan/70">Items</p>
          <Meander className="my-4" />
          <ul className="divide-y divide-edge">
            {order.items.map((item) => (
              <li key={item.id} className="flex items-start justify-between gap-4 py-3">
                <div className="min-w-0">
                  <p className="text-sm text-ink">{item.productTitle}</p>
                  <p className="text-xs text-faint">
                    {item.variantName} · {item.sku} · ×{item.quantity} @{" "}
                    {formatMoney(item.unitPriceCents, order.currency)}
                  </p>
                </div>
                <p className="shrink-0 text-sm text-gold">
                  {formatMoney(item.totalCents, order.currency)}
                </p>
              </li>
            ))}
          </ul>
          <Meander className="my-4" />
          <dl className="space-y-1.5 text-sm">
            <Row label="Subtotal" value={formatMoney(order.subtotalCents, order.currency)} />
            <Row label="Tax" value={formatMoney(order.taxCents, order.currency)} />
            <Row label="Shipping" value={formatMoney(order.shippingCents, order.currency)} />
            <div className="flex justify-between border-t border-edge pt-2">
              <dt className="inscription text-[0.66rem] text-marble">Total</dt>
              <dd className="inscription text-gold">
                {formatMoney(order.totalCents, order.currency)}
              </dd>
            </div>
          </dl>
        </Panel>

        <div className="space-y-6">
          <Panel className="p-6">
            <p className="inscription text-[0.64rem] text-cyan/70">Customer</p>
            <Meander className="my-4" />
            <p className="break-all text-sm text-ink">{order.email}</p>
            <address className="mt-3 text-sm not-italic text-muted">
              {Object.entries(order.shippingAddress)
                .filter(([, value]) => value)
                .map(([key, value]) => (
                  <span key={key} className="block">
                    {String(value)}
                  </span>
                ))}
            </address>
          </Panel>

          <Panel className="p-6">
            <p className="inscription text-[0.64rem] text-cyan/70">History</p>
            <Meander className="my-4" />
            <ol className="space-y-3">
              {order.events.map((event, index) => (
                <li key={index}>
                  <p className="text-sm text-ink">
                    {ORDER_STATUS_LABELS[event.status] ?? event.status}
                  </p>
                  <p className="text-xs text-faint">{formatDate(event.createdAt, true)}</p>
                  {event.note ? (
                    <p className="mt-0.5 text-xs text-muted">{event.note}</p>
                  ) : null}
                </li>
              ))}
            </ol>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <dt className="text-muted">{label}</dt>
      <dd className="text-ink">{value}</dd>
    </div>
  );
}
