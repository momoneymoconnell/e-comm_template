"use client";

/**
 * Variant picker and add-to-cart control.
 *
 * The only interactive part of the product page, so the only part shipped to
 * the browser as JavaScript.
 */

import { useEffect, useState } from "react";

import { Button, ErrorNote } from "@/components/ui";
import { errorMessage, useSession } from "@/components/session-provider";
import { track } from "@/lib/analytics";
import { formatMoney } from "@/lib/format";
import type { Product } from "@/lib/types";

export function AddToCart({ product }: { product: Product }) {
  const { addToCart } = useSession();

  // Default to the first variant that is actually buyable. Defaulting to
  // variants[0] regardless means a product whose small size is sold out opens
  // with a disabled button and looks broken.
  const [selectedId, setSelectedId] = useState<string>(
    () => (product.variants.find((v) => v.inStock) ?? product.variants[0])?.id ?? "",
  );
  const [quantity, setQuantity] = useState(1);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState(false);

  const selected = product.variants.find((v) => v.id === selectedId) ?? null;

  useEffect(() => {
    track("product_view", { productSlug: product.slug });
  }, [product.slug]);

  // Clear the "Added" confirmation after a moment. The timer is cancelled on
  // unmount, because setting state on an unmounted component is a leak and a
  // React warning.
  useEffect(() => {
    if (!added) return;
    const timer = setTimeout(() => setAdded(false), 2400);
    return () => clearTimeout(timer);
  }, [added]);

  if (product.variants.length === 0) {
    return <p className="text-sm text-faint">This product has no variants to buy yet.</p>;
  }

  async function handleAdd() {
    if (!selected) return;
    setPending(true);
    setError(null);
    try {
      await addToCart(selected.id, quantity);
      setAdded(true);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setPending(false);
    }
  }

  const discounted =
    selected?.compareAtPriceCents != null &&
    selected.compareAtPriceCents > selected.priceCents;

  return (
    <div className="space-y-6">
      <div className="flex items-baseline gap-3">
        <p className="inscription text-xl text-gold">
          {selected ? formatMoney(selected.priceCents, selected.currency) : "—"}
        </p>
        {discounted && selected?.compareAtPriceCents ? (
          <p className="text-sm text-faint line-through">
            {formatMoney(selected.compareAtPriceCents, selected.currency)}
          </p>
        ) : null}
      </div>

      {product.variants.length > 1 ? (
        <fieldset>
          <legend className="inscription mb-3 text-[0.64rem] text-cyan/80">Option</legend>
          <div className="flex flex-wrap gap-2">
            {product.variants.map((variant) => {
              const active = variant.id === selectedId;
              return (
                <button
                  key={variant.id}
                  type="button"
                  onClick={() => setSelectedId(variant.id)}
                  disabled={!variant.inStock}
                  aria-pressed={active}
                  className={`inscription rounded-md border px-4 py-2 text-[0.64rem] transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                    active
                      ? "border-neon bg-neon/10 text-neon"
                      : "border-edge-bright text-ink hover:border-cyan hover:text-cyan"
                  }`}
                >
                  {variant.name}
                  {!variant.inStock ? " · sold out" : ""}
                </button>
              );
            })}
          </div>
        </fieldset>
      ) : null}

      <div className="flex flex-wrap items-end gap-4">
        <div>
          <label
            htmlFor="quantity"
            className="inscription mb-2 block text-[0.64rem] text-cyan/80"
          >
            Quantity
          </label>
          <input
            id="quantity"
            type="number"
            min={1}
            max={100}
            value={quantity}
            onChange={(event) => {
              // Clamped here as well as in the API. The API is the real
              // guarantee; this just stops the UI showing an impossible value.
              const next = Number.parseInt(event.target.value, 10);
              setQuantity(Number.isNaN(next) ? 1 : Math.min(100, Math.max(1, next)));
            }}
            className="w-24 rounded-md border border-edge bg-night px-3 py-2.5 text-sm text-ink focus:border-cyan focus:outline-none"
          />
        </div>

        <Button
          type="button"
          onClick={handleAdd}
          disabled={pending || !selected?.inStock}
          className="min-w-44"
        >
          {pending
            ? "Adding…"
            : added
              ? "Added ✓"
              : selected?.inStock
                ? "Add to cart"
                : "Sold out"}
        </Button>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {/* aria-live means a screen reader announces the confirmation. Without
          it the only feedback is a button label change nobody hears. */}
      <p aria-live="polite" className="sr-only">
        {added ? "Added to cart." : ""}
      </p>
    </div>
  );
}
