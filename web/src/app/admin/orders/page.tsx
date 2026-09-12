"use client";

/**
 * Order management: browse, filter and search every order.
 */

import Link from "next/link";
import { useState } from "react";

import { Td, TableWrap, Th } from "@/components/admin-ui";
import { Button, EmptyState, ErrorNote, Pill, Skeleton } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import { useAsyncData } from "@/lib/use-async";
import {
  ORDER_STATUS_LABELS,
  ORDER_STATUS_TONE,
  formatDate,
  formatMoney,
} from "@/lib/format";
import type { OrderSummary, Page } from "@/lib/types";

const STATUSES = [
  "pending_payment",
  "paid",
  "fulfilled",
  "delivered",
  "cancelled",
  "refunded",
] as const;

const PAGE_SIZE = 25;

export default function AdminOrdersPage() {
  const [status, setStatus] = useState<string>("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const { data, error, loading, stale } = useAsyncData<Page<OrderSummary>>(() => {
    const query = new URLSearchParams({
      page: String(page),
      pageSize: String(PAGE_SIZE),
    });
    if (status) query.set("status", status);
    if (search.trim()) query.set("search", search.trim());
    return apiFetch<Page<OrderSummary>>(`/orders/admin?${query}`);
  }, [page, status, search]);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-48 flex-1">
          <label htmlFor="order-search" className="inscription mb-2 block text-[0.58rem] text-cyan/70">
            Search
          </label>
          <input
            id="order-search"
            type="search"
            value={search}
            placeholder="Order number or email"
            onChange={(event) => {
              setSearch(event.target.value);
              // Any filter change must return to page 1, or a search that
              // matches three orders shows an empty page 4.
              setPage(1);
            }}
            className="w-full rounded-md border border-edge bg-night px-4 py-2 text-sm text-ink placeholder:text-faint focus:border-cyan focus:outline-none"
          />
        </div>

        <div>
          <label htmlFor="order-status" className="inscription mb-2 block text-[0.58rem] text-cyan/70">
            Status
          </label>
          <select
            id="order-status"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setPage(1);
            }}
            className="rounded-md border border-edge bg-night px-4 py-2 text-sm text-ink focus:border-cyan focus:outline-none"
          >
            <option value="">All statuses</option>
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {ORDER_STATUS_LABELS[value]}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : data && data.items.length > 0 ? (
        <div className={stale ? "opacity-60 transition-opacity" : ""}>
          <TableWrap>
            <thead>
              <tr>
                <Th>Order</Th>
                <Th>Placed</Th>
                <Th>Customer</Th>
                <Th>Items</Th>
                <Th>Status</Th>
                <Th className="text-right">Total</Th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((order) => (
                <tr key={order.id} className="transition-colors hover:bg-raised/50">
                  <Td>
                    <Link
                      href={`/admin/orders/${order.id}`}
                      className="inscription text-[0.66rem] text-marble transition-colors hover:text-neon"
                    >
                      {order.orderNumber}
                    </Link>
                  </Td>
                  <Td className="whitespace-nowrap text-xs text-muted">
                    {formatDate(order.createdAt, true)}
                  </Td>
                  <Td className="text-xs text-muted">{order.email}</Td>
                  <Td className="text-xs">{order.itemCount}</Td>
                  <Td>
                    <Pill className={ORDER_STATUS_TONE[order.status] ?? "border-edge text-muted"}>
                      {ORDER_STATUS_LABELS[order.status] ?? order.status}
                    </Pill>
                  </Td>
                  <Td className="whitespace-nowrap text-right text-gold">
                    {formatMoney(order.totalCents, order.currency)}
                  </Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>

          <div className="flex items-center justify-between text-xs text-muted">
            <span>
              {data.total} order{data.total === 1 ? "" : "s"}
            </span>
            <div className="flex items-center gap-3">
              <Button
                tone="ghost"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </Button>
              <span>
                {page} / {totalPages}
              </span>
              <Button
                tone="ghost"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          </div>
        </div>
      ) : (
        <EmptyState title="No orders">
          {search || status
            ? "Nothing matches those filters."
            : "Orders will appear here once customers start buying."}
        </EmptyState>
      )}
    </div>
  );
}
