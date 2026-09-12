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
import { Container, Meander } from "@/components/ui";
import { serverFetch } from "@/lib/api";
import type { Product } from "@/lib/types";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const product = await serverFetch<Product>(`/catalog/products/${slug}`);
  if (!product) return { title: "Not found" };
  return {
    title: product.title,
    description: product.subtitle ?? product.description?.slice(0, 150) ?? undefined,
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
          {product.imageUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={product.imageUrl}
              alt={product.title}
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
    </Container>
  );
}
