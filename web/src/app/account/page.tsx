"use client";

/**
 * Customer account: profile, order history and active sessions.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useSession } from "@/components/session-provider";
import {
  Button,
  ButtonLink,
  Container,
  EmptyState,
  ErrorNote,
  Meander,
  Panel,
  Pill,
  SectionTitle,
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
import type { OrderSummary, Page } from "@/lib/types";

export default function AccountPage() {
  const { user, loading, signOut } = useSession();
  const router = useRouter();

  // Fetched only once the session is known: an anonymous request would just
  // 401, and the effect below redirects that case away anyway.
  const {
    data: orders,
    error,
    loading: loadingOrders,
  } = useAsyncData<Page<OrderSummary> | null>(
    async () => (user ? apiFetch<Page<OrderSummary>>("/orders?pageSize=20") : null),
    [user?.id],
  );

  // Redirect only once the session check has settled. Redirecting while
  // `loading` is true would bounce a signed-in user off their own account page
  // on every hard refresh.
  useEffect(() => {
    if (!loading && !user) router.replace("/login?next=/account");
  }, [loading, user, router]);

  if (loading || !user) {
    return (
      <Container className="py-16">
        <Skeleton className="h-8 w-48" />
        <div className="mt-8 space-y-3">
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
        </div>
      </Container>
    );
  }

  return (
    <Container className="py-16">
      <SectionTitle kicker="Your account">{user.fullName || user.email}</SectionTitle>

      <div className="grid gap-8 lg:grid-cols-[1fr_18rem]">
        <div>
          <h2 className="inscription mb-4 text-[0.72rem] text-cyan/80">Order history</h2>

          {error ? <ErrorNote>{error}</ErrorNote> : null}

          {loadingOrders ? (
            <div className="space-y-3">
              <Skeleton className="h-20" />
              <Skeleton className="h-20" />
            </div>
          ) : orders && orders.items.length > 0 ? (
            <ul className="space-y-3">
              {orders.items.map((order) => (
                <li key={order.id}>
                  <Link
                    href={`/account/orders/${order.id}`}
                    className="surface flex flex-wrap items-center justify-between gap-4 p-5 transition-colors hover:border-neon"
                  >
                    <div>
                      <p className="inscription text-[0.72rem] text-marble">
                        {order.orderNumber}
                      </p>
                      <p className="mt-1 text-xs text-faint">
                        {formatDate(order.createdAt, true)} · {order.itemCount} item
                        {order.itemCount === 1 ? "" : "s"}
                      </p>
                    </div>
                    <div className="flex items-center gap-4">
                      <Pill className={ORDER_STATUS_TONE[order.status] ?? "border-edge text-muted"}>
                        {ORDER_STATUS_LABELS[order.status] ?? order.status}
                      </Pill>
                      <span className="text-sm text-gold">
                        {formatMoney(order.totalCents, order.currency)}
                      </span>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              title="No orders yet"
              action={<ButtonLink href="/shop" className="mt-2">Browse the shop</ButtonLink>}
            >
              Your orders will appear here once you have placed one.
            </EmptyState>
          )}
        </div>

        <Panel className="h-fit p-6">
          <p className="inscription text-[0.68rem] text-cyan/80">Details</p>
          <Meander className="my-4" />

          <dl className="space-y-3 text-sm">
            <div>
              <dt className="text-xs text-faint">Email</dt>
              <dd className="break-all text-ink">{user.email}</dd>
            </div>
            <div>
              <dt className="text-xs text-faint">Member since</dt>
              <dd className="text-ink">{formatDate(user.createdAt)}</dd>
            </div>
            <div>
              <dt className="text-xs text-faint">Role</dt>
              <dd className="text-ink capitalize">{user.role}</dd>
            </div>
          </dl>

          <Meander className="my-5" />

          {user.role === "admin" ? (
            <ButtonLink href="/admin" tone="ghost" className="mb-3 w-full">
              Admin console
            </ButtonLink>
          ) : null}

          <Button
            tone="ghost"
            className="w-full"
            onClick={async () => {
              await signOut();
              router.push("/");
              router.refresh();
            }}
          >
            Sign out
          </Button>
        </Panel>
      </div>
    </Container>
  );
}
