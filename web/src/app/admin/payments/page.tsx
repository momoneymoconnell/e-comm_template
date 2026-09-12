"use client";

/**
 * Payments and refunds.
 *
 * A refund moves real money, so it is confirmed before it is sent and is
 * attributed server-side to the admin who issued it.
 */

import { useState } from "react";

import { Stat, TableWrap, Td, Th } from "@/components/admin-ui";
import { errorMessage } from "@/components/session-provider";
import { Button, EmptyState, ErrorNote, Pill, Skeleton } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import { useAsyncData } from "@/lib/use-async";
import { formatDate, formatMoney, formatNumber } from "@/lib/format";
import type { Page, Payment, PaymentStats } from "@/lib/types";

const PAGE_SIZE = 25;

const STATUS_TONE: Record<string, string> = {
  succeeded: "border-ok/40 text-ok",
  pending: "border-warn/40 text-warn",
  failed: "border-danger/40 text-danger",
  cancelled: "border-edge text-faint",
  refunded: "border-neon/40 text-neon",
};

export default function AdminPaymentsPage() {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [page, setPage] = useState(1);

  const {
    data: result,
    error: loadError,
    loading,
    reload,
  } = useAsyncData<{ payments: Page<Payment>; stats: PaymentStats }>(async () => {
    const [payments, stats] = await Promise.all([
      apiFetch<Page<Payment>>(`/payments/admin?page=${page}&pageSize=${PAGE_SIZE}`),
      apiFetch<PaymentStats>("/payments/admin/stats"),
    ]);
    return { payments, stats };
  }, [page]);

  const data = result?.payments ?? null;
  const stats = result?.stats ?? null;
  const error = actionError ?? loadError;

  async function refund(payment: Payment) {
    // A native confirm is deliberate here. This sends money back through
    // Stripe and cannot be undone; a bespoke modal would look nicer and be
    // easier to click through by accident.
    const ok = window.confirm(
      `Refund ${formatMoney(payment.amountReceivedCents, payment.currency)} for ` +
        `order ${payment.orderNumber}? This cannot be undone.`,
    );
    if (!ok) return;

    setBusyId(payment.id);
    setActionError(null);
    try {
      await apiFetch<unknown>(`/payments/admin/${payment.id}/refund`, {
        method: "POST",
        json: { reason: "Refunded from the admin console." },
      });
      reload();
    } catch (caught) {
      setActionError(errorMessage(caught));
    } finally {
      setBusyId(null);
    }
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Captured"
          value={stats ? formatMoney(stats.capturedCents) : "—"}
          loading={loading}
          tone="good"
        />
        <Stat
          label="Refunded"
          value={stats ? formatMoney(stats.refundedCents) : "—"}
          loading={loading}
          tone={stats && stats.refundedCents > 0 ? "warn" : "default"}
        />
        <Stat
          label="Net"
          value={stats ? formatMoney(stats.netCents) : "—"}
          loading={loading}
        />
        <Stat
          label="Failed"
          value={stats ? formatNumber(stats.failed) : "—"}
          loading={loading}
          tone={stats && stats.failed > 0 ? "bad" : "default"}
        />
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : data && data.items.length > 0 ? (
        <>
          <TableWrap>
            <thead>
              <tr>
                <Th>Order</Th>
                <Th>Created</Th>
                <Th>Customer</Th>
                <Th>Status</Th>
                <Th className="text-right">Amount</Th>
                <Th className="text-right">Actions</Th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((payment) => (
                <tr key={payment.id} className="transition-colors hover:bg-raised/50">
                  <Td>
                    <span className="inscription text-[0.64rem] text-marble">
                      {payment.orderNumber}
                    </span>
                    <span className="block font-mono text-[0.65rem] text-faint">
                      {payment.paymentIntentId}
                    </span>
                  </Td>
                  <Td className="whitespace-nowrap text-xs text-muted">
                    {formatDate(payment.createdAt, true)}
                  </Td>
                  <Td className="text-xs text-muted">{payment.email ?? "—"}</Td>
                  <Td>
                    <Pill className={STATUS_TONE[payment.status] ?? "border-edge text-muted"}>
                      {payment.status}
                    </Pill>
                    {payment.failureMessage ? (
                      <span className="mt-1 block text-[0.65rem] text-danger">
                        {payment.failureMessage}
                      </span>
                    ) : null}
                  </Td>
                  <Td className="whitespace-nowrap text-right text-gold">
                    {formatMoney(payment.amountCents, payment.currency)}
                  </Td>
                  <Td className="text-right">
                    {payment.status === "succeeded" ? (
                      <Button
                        tone="danger"
                        disabled={busyId === payment.id}
                        onClick={() => void refund(payment)}
                      >
                        Refund
                      </Button>
                    ) : (
                      <span className="text-xs text-faint">—</span>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>

          <div className="flex items-center justify-between text-xs text-muted">
            <span>
              {data.total} payment{data.total === 1 ? "" : "s"}
            </span>
            <div className="flex items-center gap-3">
              <Button tone="ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
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
        </>
      ) : (
        <EmptyState title="No payments yet">
          Payments appear here once a customer checks out.
        </EmptyState>
      )}
    </div>
  );
}
