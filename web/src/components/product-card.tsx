/**
 * A product tile for grid listings. Server component.
 */

import Link from "next/link";

import { RatingBadge } from "@/components/reviews";
import { mediaUrl } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import type { Product } from "@/lib/types";

export function ProductCard({ product }: { product: Product }) {
  // The "from" price is the cheapest variant. Showing the first variant's
  // price instead would understate or overstate depending on sort order, and
  // a price that changes on the product page erodes trust immediately.
  const prices = product.variants.map((variant) => variant.priceCents);
  const fromPrice = prices.length > 0 ? Math.min(...prices) : null;
  const currency = product.variants[0]?.currency ?? "usd";
  const anyInStock = product.variants.some((variant) => variant.inStock);
  const primaryImage = product.images?.[0];
  const onSale = product.variants.some(
    (v) => v.compareAtPriceCents !== null && v.compareAtPriceCents > v.priceCents,
  );

  return (
    <Link
      href={`/shop/${product.slug}`}
      className="surface group flex flex-col overflow-hidden transition-colors hover:border-neon"
    >
      <div className="relative aspect-[4/3] overflow-hidden border-b border-edge bg-night">
        {primaryImage || product.imageUrl ? (
          // A plain <img>: product images come from arbitrary URLs entered in
          // the admin console, and next/image would need every one of those
          // hosts allowlisted in next.config before it would render at all.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={mediaUrl(primaryImage?.thumbUrl ?? product.imageUrl) ?? ""}
            alt={primaryImage?.alt ?? ""}
            loading="lazy"
            className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105"
          />
        ) : (
          <PlaceholderArt />
        )}

        <div className="absolute left-3 top-3 flex gap-2">
          {onSale ? (
            <span className="rounded-full bg-neon px-2.5 py-0.5 text-[0.6rem] uppercase tracking-[0.15em] text-void">
              Sale
            </span>
          ) : null}
          {!anyInStock ? (
            <span className="rounded-full border border-faint/50 bg-void/80 px-2.5 py-0.5 text-[0.6rem] uppercase tracking-[0.15em] text-faint">
              Sold out
            </span>
          ) : null}
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-1.5 p-5">
        {product.category ? (
          <span className="inscription text-[0.6rem] text-cyan/70">
            {product.category.name}
          </span>
        ) : null}

        <h3 className="inscription text-[0.8rem] text-marble transition-colors group-hover:text-neon">
          {product.title}
        </h3>

        {product.subtitle ? (
          <p className="line-clamp-2 text-sm text-muted">{product.subtitle}</p>
        ) : null}

        <RatingBadge average={product.ratingAverage} count={product.ratingCount} />

        <p className="mt-auto pt-3 text-sm text-gold">
          {fromPrice !== null ? (
            <>
              {product.variants.length > 1 ? (
                <span className="text-faint">from </span>
              ) : null}
              {formatMoney(fromPrice, currency)}
            </>
          ) : (
            <span className="text-faint">Unpriced</span>
          )}
        </p>
      </div>
    </Link>
  );
}

/**
 * Stand-in artwork for products with no image.
 *
 * A drawn placeholder rather than a grey box: an empty catalogue is the first
 * thing anyone sees on a fresh install, and it should still look deliberate.
 */
function PlaceholderArt() {
  return (
    <div
      aria-hidden
      className="grid h-full w-full place-items-center bg-[radial-gradient(ellipse_at_50%_20%,#2a1b5e_0%,#140e2e_70%)]"
    >
      <div className="relative">
        <span className="vapor-sun block h-16 w-16 rounded-full opacity-80" />
        <span className="fluted absolute -bottom-1 left-1/2 h-10 w-8 -translate-x-1/2 rounded-t-sm border-x border-t border-edge-bright/60 bg-gradient-to-b from-panel/80 to-transparent" />
      </div>
    </div>
  );
}
