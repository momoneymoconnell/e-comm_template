"use client";

/**
 * The cart.
 *
 * A client component: it is entirely interactive, and its contents come from
 * the shared session context, which is already loaded by the time this renders.
 */

import Link from "next/link";
import { useState } from "react";

import { errorMessage, useSession } from "@/components/session-provider";
import {
  ButtonLink,
  Container,
  EmptyState,
  ErrorNote,
  Meander,
  Panel,
  SectionTitle,
} from "@/components/ui";
import { formatMoney } from "@/lib/format";

export default function CartPage() {
  const { cart, setCartQuantity, loading } = useSession();
  const [busyItem, setBusyItem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function change(itemId: string, quantity: number) {
    setBusyItem(itemId);
    setError(null);
    try {
      await setCartQuantity(itemId, quantity);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusyItem(null);
    }
  }

  if (loading && !cart) {
    return (
      <Container className="py-16">
        <SectionTitle kicker="Your selections">Cart</SectionTitle>
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton h-24 rounded-lg" />
          ))}
        </div>
      </Container>
    );
  }

  if (!cart || cart.items.length === 0) {
    return (
      <Container className="py-16">
        <SectionTitle kicker="Your selections">Cart</SectionTitle>
        <EmptyState
          title="Your cart is empty"
          action={<ButtonLink href="/shop" className="mt-2">Browse the shop</ButtonLink>}
        >
          Nothing selected yet.
        </EmptyState>
      </Container>
    );
  }

  return (
    <Container className="py-16">
      <SectionTitle kicker="Your selections">Cart</SectionTitle>

      {error ? (
        <div className="mb-6">
          <ErrorNote>{error}</ErrorNote>
        </div>
      ) : null}

      {cart.hasUnavailableItems ? (
        <div className="mb-6 rounded-md border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn">
          Some items are no longer available and are excluded from the total.
          Remove them to continue.
        </div>
      ) : null}

      <div className="grid gap-10 lg:grid-cols-[1fr_22rem]">
        <ul className="space-y-3">
          {cart.items.map((item) => (
            <li
              key={item.id}
              className={`surface flex gap-4 p-4 ${item.available ? "" : "opacity-60"}`}
            >
              <div className="h-20 w-20 shrink-0 overflow-hidden rounded-md border border-edge bg-night">
                {item.imageUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={item.imageUrl} alt="" className="h-full w-full object-cover" />
                ) : (
                  <div aria-hidden className="vapor-sun m-4 h-12 w-12 rounded-full opacity-60" />
                )}
              </div>

              <div className="min-w-0 flex-1">
                {item.productSlug ? (
                  <Link
                    href={`/shop/${item.productSlug}`}
                    className="inscription text-[0.72rem] text-marble transition-colors hover:text-neon"
                  >
                    {item.productTitle}
                  </Link>
                ) : (
                  <p className="inscription text-[0.72rem] text-faint">{item.productTitle}</p>
                )}
                <p className="mt-1 text-xs text-muted">
                  {item.variantName} · {item.sku}
                </p>
                {!item.available ? (
                  <p className="mt-1 text-xs text-warn">No longer available</p>
                ) : null}

                <div className="mt-3 flex items-center gap-3">
                  <label htmlFor={`qty-${item.id}`} className="sr-only">
                    Quantity for {item.productTitle}
                  </label>
                  <input
                    id={`qty-${item.id}`}
                    type="number"
                    min={0}
                    max={100}
                    value={item.quantity}
                    disabled={busyItem === item.id}
                    onChange={(event) => {
                      const next = Number.parseInt(event.target.value, 10);
                      if (!Number.isNaN(next) && next >= 0 && next <= 100) {
                        void change(item.id, next);
                      }
                    }}
                    className="w-20 rounded-md border border-edge bg-night px-2 py-1.5 text-sm text-ink focus:border-cyan focus:outline-none"
                  />
                  <button
                    type="button"
                    onClick={() => void change(item.id, 0)}
                    disabled={busyItem === item.id}
                    className="text-xs text-faint underline-offset-4 transition-colors hover:text-danger hover:underline"
                  >
                    Remove
                  </button>
                </div>
              </div>

              <p className="shrink-0 text-sm text-gold">
                {formatMoney(item.totalCents, cart.currency)}
              </p>
            </li>
          ))}
        </ul>

        <Panel className="h-fit p-6 lg:sticky lg:top-28">
          <p className="inscription text-[0.68rem] text-cyan/80">Summary</p>
          <Meander className="my-4" />

          <dl className="space-y-2.5 text-sm">
            <Row label="Subtotal" value={formatMoney(cart.subtotalCents, cart.currency)} />
            {cart.taxCents > 0 ? (
              <Row label="Tax" value={formatMoney(cart.taxCents, cart.currency)} />
            ) : null}
            <Row
              label="Shipping"
              value={
                cart.shippingCents === 0
                  ? "Free"
                  : formatMoney(cart.shippingCents, cart.currency)
              }
            />
          </dl>

          <Meander className="my-4" />

          <div className="flex items-baseline justify-between">
            <span className="inscription text-[0.7rem] text-marble">Total</span>
            <span className="inscription text-lg text-gold">
              {formatMoney(cart.totalCents, cart.currency)}
            </span>
          </div>

          <ButtonLink
            href="/checkout"
            className={`mt-6 w-full ${cart.hasUnavailableItems ? "pointer-events-none opacity-40" : ""}`}
          >
            Checkout
          </ButtonLink>

          <Link
            href="/shop"
            className="mt-4 block text-center text-xs text-muted underline-offset-4 transition-colors hover:text-cyan hover:underline"
          >
            Keep shopping
          </Link>
        </Panel>
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
