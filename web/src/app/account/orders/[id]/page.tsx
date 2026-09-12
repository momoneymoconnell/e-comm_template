"use client";

/**
 * A single order, as the customer sees it.
 *
 * Ownership is enforced by the API: an order belonging to someone else returns
 * 404, not 403, so guessing IDs reveals nothing about what exists.
 */

import Link from "next/link";
import { use, useEffect, useState } from "react";

import { errorMessage } from "@/components/session-provider";
import {
  Container,
  EmptyState,
  Meander,
  Panel,
  Pill,
  SectionTitle,
  Skeleton,
} from "@/components/ui";
import { apiFetch } from "@/lib/api";
import {
  ORDER_STATUS_LABELS,
  ORDER_STATUS_TONE,
  formatDate,
  formatMoney,
} from "@/lib/format";
import type { Order } from "@/lib/types";

export default function OrderDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [order, setOrder] = useState<Order | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        setOrder(await apiFetch<Order>(`/orders/${id}`));
      } catch (caught) {
        setError(errorMessage(caught));
      } finally {
        setLoading(false);
      }
    })();
  }, [id]);

  if (loading) {
    return (
      <Container className="py-16">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="mt-6 h-64" />
      </Container>
    );
  }

  if (error || !order) {
    return (
      <Container className="py-16">
        <EmptyState title="Order not found">
          {error ?? "This order does not exist, or it is not yours."}
        </EmptyState>
        <p className="mt-6 text-center text-xs">
          <Link href="/account" className="text-cyan underline-offset-4 hover:underline">
            Back to your account
          </Link>
        </p>
      </Container>
    );
  }

  return (
    <Container className="py-16">
      <SectionTitle kicker={formatDate(order.createdAt, true)}>
        {order.orderNumber}
      </SectionTitle>

      <div className="mb-8">
        <Pill className={ORDER_STATUS_TONE[order.status] ?? "border-edge text-muted"}>
          {ORDER_STATUS_LABELS[order.status] ?? order.status}
        </Pill>
      </div>

      <div className="grid gap-8 lg:grid-cols-[1fr_20rem]">
        <Panel className="p-6">
          <p className="inscription text-[0.68rem] text-cyan/80">Items</p>
          <Meander className="my-4" />
          <ul className="divide-y divide-edge">
            {order.items.map((item) => (
              <li key={item.id} className="flex items-start justify-between gap-4 py-4">
                <div className="min-w-0">
                  <p className="text-sm text-ink">{item.productTitle}</p>
                  <p className="mt-0.5 text-xs text-faint">
                    {item.variantName} · {item.sku} · ×{item.quantity}
                  </p>
                </div>
                <p className="shrink-0 text-sm text-gold">
                  {formatMoney(item.totalCents, order.currency)}
                </p>
              </li>
            ))}
          </ul>

          <Meander className="my-4" />

          <dl className="space-y-2 text-sm">
            <Row label="Subtotal" value={formatMoney(order.subtotalCents, order.currency)} />
            {order.taxCents > 0 ? (
              <Row label="Tax" value={formatMoney(order.taxCents, order.currency)} />
            ) : null}
            <Row
              label="Shipping"
              value={
                order.shippingCents === 0
                  ? "Free"
                  : formatMoney(order.shippingCents, order.currency)
              }
            />
            <div className="flex justify-between border-t border-edge pt-2">
              <dt className="inscription text-[0.7rem] text-marble">Total</dt>
              <dd className="inscription text-gold">
                {formatMoney(order.totalCents, order.currency)}
              </dd>
            </div>
          </dl>
        </Panel>

        <div className="space-y-6">
          <Panel className="p-6">
            <p className="inscription text-[0.68rem] text-cyan/80">Shipping to</p>
            <Meander className="my-4" />
            <address className="text-sm not-italic text-muted">
              {Object.entries(order.shippingAddress)
                .filter(([, value]) => value)
                .map(([key, value]) => (
                  <span key={key} className="block">
                    {String(value)}
                  </span>
                ))}
            </address>
          </Panel>

          {order.events.length > 0 ? (
            <Panel className="p-6">
              <p className="inscription text-[0.68rem] text-cyan/80">History</p>
              <Meander className="my-4" />
              <ol className="space-y-3">
                {order.events.map((event, index) => (
                  <li key={index} className="text-sm">
                    <p className="text-ink">
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
          ) : null}
        </div>
      </div>
    </Container>
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
