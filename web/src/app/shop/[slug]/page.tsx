/**
 * Product detail.
 *
 * The page shell is a server component so the content is in the HTML; the
 * variant picker and add-to-cart button are a small client island, because
 * they need state.
 */

import { headers } from "next/headers";
import { notFound } from "next/navigation";
import Link from "next/link";

import { AddToCart } from "@/components/add-to-cart";
import { ProductReviews, RatingBadge } from "@/components/reviews";
import { Container, Meander } from "@/components/ui";
import { mediaUrl, serverFetch } from "@/lib/api";
import type { Product } from "@/lib/types";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const product = await serverFetch<Product>(`/catalog/products/${slug}`);
  if (!product) return { title: "Not found" };
  const image = product.images?.[0]?.url ?? product.imageUrl;
  const description =
    product.subtitle ?? product.description?.slice(0, 150) ?? undefined;

  return {
    title: product.title,
    description,
    // Open Graph controls how the page looks when someone pastes the link into
    // a chat or posts it. Without it the preview is a bare URL, which converts
    // noticeably worse than a card with a picture and a price.
    openGraph: {
      title: product.title,
      description,
      type: "website",
      images: image ? [{ url: mediaUrl(image) ?? "" }] : undefined,
    },
    twitter: {
      card: image ? "summary_large_image" : "summary",
      title: product.title,
      description,
    },
  };
}

export default async function ProductPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const cookieHeader = (await headers()).get("cookie") ?? undefined;
  const product = await serverFetch<Product>(`/catalog/products/${slug}`, cookieHeader);

  // The API already returns 404 for drafts and archived products, so this
  // covers both "does not exist" and "not yet published" without the page
  // needing to know the difference.
  if (!product) notFound();

  // Prefer the gallery; `imageUrl` is the legacy single-image column, kept so
  // products created before uploads existed still render.
  const hero = product.images?.[0];
  const heroUrl = mediaUrl(hero?.url ?? product.imageUrl);

  // The variant a search result should quote: the one a shopper would actually
  // pay least for.
  const cheapest = product.variants.reduce<(typeof product.variants)[number] | null>(
    (lowest, variant) =>
      lowest === null || variant.priceCents < lowest.priceCents ? variant : lowest,
    null,
  );

  return (
    <Container className="py-14">
      <nav aria-label="Breadcrumb" className="mb-8 text-xs text-faint">
        <Link href="/shop" className="transition-colors hover:text-cyan">
          Shop
        </Link>
        {product.category ? (
          <>
            <span aria-hidden className="mx-2">/</span>
            <Link
              href={`/shop?category=${encodeURIComponent(product.category.slug)}`}
              className="transition-colors hover:text-cyan"
            >
              {product.category.name}
            </Link>
          </>
        ) : null}
        <span aria-hidden className="mx-2">/</span>
        <span className="text-muted">{product.title}</span>
      </nav>

      <div className="grid gap-10 lg:grid-cols-2 lg:gap-14">
        <div className="surface relative aspect-square overflow-hidden">
          {heroUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={heroUrl}
              alt={hero?.alt ?? product.title}
              className="h-full w-full object-cover"
            />
          ) : (
            <div
              aria-hidden
              className="grid h-full w-full place-items-center bg-[radial-gradient(ellipse_at_50%_25%,#2a1b5e_0%,#140e2e_70%)]"
            >
              <div className="relative">
                <span className="vapor-sun block h-32 w-32 rounded-full opacity-80" />
                <span className="fluted absolute -bottom-2 left-1/2 h-20 w-14 -translate-x-1/2 rounded-t-sm border-x border-t border-edge-bright/60 bg-gradient-to-b from-panel/80 to-transparent" />
              </div>
            </div>
          )}
        </div>

        <div>
          {product.images && product.images.length > 1 ? (
            <div className="mb-6 grid grid-cols-4 gap-2 lg:hidden">
              {product.images.slice(1, 5).map((image) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  key={image.id}
                  src={mediaUrl(image.thumbUrl) ?? ""}
                  alt={image.alt ?? ""}
                  className="aspect-square w-full rounded-md border border-edge object-cover"
                />
              ))}
            </div>
          ) : null}

          {product.category ? (
            <p className="inscription text-[0.64rem] text-cyan/80">
              {product.category.name}
            </p>
          ) : null}

          <h1 className="inscription mt-3 text-2xl text-marble sm:text-3xl">
            {product.title}
          </h1>

          {product.subtitle ? (
            <p className="mt-3 text-base text-muted">{product.subtitle}</p>
          ) : null}

          {product.ratingCount > 0 ? (
            <a href="#reviews" className="mt-3 inline-block">
              <RatingBadge
                average={product.ratingAverage}
                count={product.ratingCount}
                size={15}
              />
            </a>
          ) : null}

          <Meander className="my-7 max-w-[8rem]" />

          {product.description ? (
            <div className="whitespace-pre-line text-sm leading-relaxed text-ink/90">
              {product.description}
            </div>
          ) : null}

          <div className="mt-9">
            <AddToCart product={product} />
          </div>
        </div>
      </div>

      <ProductReviews slug={product.slug} />

      {/*
        Product structured data.

        This is what lets a search result show a price, availability and a star
        rating rather than just a title and a snippet. It is plain JSON-LD in a
        script tag - no library, no build step.

        Only emitted when there is a real price to state; incomplete structured
        data is worse than none, because search engines will reject the whole
        block and may distrust the page.
      */}
      {cheapest ? (
        <script
          type="application/ld+json"
          // The payload is JSON.stringify of our own data, not user markup.
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              "@context": "https://schema.org",
              "@type": "Product",
              name: product.title,
              description: product.description ?? product.subtitle ?? undefined,
              sku: cheapest.sku,
              image: heroUrl ? [heroUrl] : undefined,
              offers: {
                "@type": "Offer",
                price: (cheapest.priceCents / 100).toFixed(2),
                priceCurrency: cheapest.currency.toUpperCase(),
                availability: cheapest.inStock
                  ? "https://schema.org/InStock"
                  : "https://schema.org/OutOfStock",
              },
              aggregateRating:
                product.ratingCount > 0 && product.ratingAverage !== null
                  ? {
                      "@type": "AggregateRating",
                      ratingValue: product.ratingAverage,
                      reviewCount: product.ratingCount,
                    }
                  : undefined,
            }),
          }}
        />
      ) : null}
    </Container>
  );
}
