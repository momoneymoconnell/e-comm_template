/**
 * Home page.
 *
 * A server component: the product strip is fetched during rendering, so the
 * HTML arrives complete rather than empty with a spinner.
 *
 * The copy is intentionally placeholder. This is the page to rewrite first
 * once the business is decided.
 */

import { headers } from "next/headers";

import { ProductCard } from "@/components/product-card";
import { Skyline } from "@/components/skyline";
import { ButtonLink, Container, Meander, SectionTitle } from "@/components/ui";
import { serverFetch } from "@/lib/api";
import type { Category, Page, Product } from "@/lib/types";

export default async function HomePage() {
  const cookieHeader = (await headers()).get("cookie") ?? undefined;

  // Fetched in parallel. Awaiting them in sequence would make the page as slow
  // as the sum of both round trips instead of the slower one.
  const [products, categories] = await Promise.all([
    serverFetch<Page<Product>>("/catalog/products?pageSize=6", cookieHeader),
    serverFetch<Category[]>("/catalog/categories", cookieHeader),
  ]);

  return (
    <>
      <Hero />

      <Container className="py-20">
        <SectionTitle kicker="The collection">Recently added</SectionTitle>

        {products && products.items.length > 0 ? (
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {products.items.map((product) => (
              <ProductCard key={product.id} product={product} />
            ))}
          </div>
        ) : (
          <div className="surface px-6 py-14 text-center">
            <p className="inscription text-sm text-marble">Nothing here yet</p>
            <p className="mx-auto mt-3 max-w-md text-sm text-muted">
              The catalogue is empty. Run{" "}
              <code className="rounded bg-void px-1.5 py-0.5 font-mono text-cyan">make seed</code>{" "}
              for placeholder products, or add your own from the admin console.
            </p>
          </div>
        )}

        {products && products.items.length > 0 ? (
          <div className="mt-10 flex justify-center">
            <ButtonLink href="/shop" tone="ghost">
              View everything
            </ButtonLink>
          </div>
        ) : null}
      </Container>

      {categories && categories.length > 0 ? (
        <Container className="pb-24">
          <SectionTitle kicker="Browse">Collections</SectionTitle>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {categories.map((category) => (
              <a
                key={category.id}
                href={`/shop?category=${encodeURIComponent(category.slug)}`}
                className="surface group flex items-center justify-between px-5 py-5 transition-colors hover:border-neon"
              >
                <span>
                  <span className="inscription block text-[0.72rem] text-marble transition-colors group-hover:text-neon">
                    {category.name}
                  </span>
                  <span className="mt-1 block text-xs text-faint">
                    {category.productCount ?? 0} item
                    {(category.productCount ?? 0) === 1 ? "" : "s"}
                  </span>
                </span>
                <span aria-hidden className="text-cyan transition-transform group-hover:translate-x-1">
                  →
                </span>
              </a>
            ))}
          </div>
        </Container>
      ) : null}
    </>
  );
}

/**
 * The hero.
 *
 * Where the two halves of the aesthetic meet: the sun and perspective grid are
 * vaporwave, the symmetrical column arcade and inscriptional type are Roman.
 */
function Hero() {
  return (
    <section className="relative isolate overflow-hidden border-b border-edge">
      {/* Background wash. */}
      <div
        aria-hidden
        className="absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_50%_-10%,#2a1b5e_0%,#120d29_45%,#0a0718_100%)]"
      />
      {/* The sun, sitting behind the wordmark.
          Raised and dimmed relative to a first pass where it sat directly
          behind the headline: the slots cut through the letterforms and the
          gradient fill lost contrast against the bright disc. It now reads as
          a backdrop rather than competing with the type. */}
      <div
        aria-hidden
        className="vapor-sun absolute left-1/2 top-2 -z-10 h-56 w-56 -translate-x-1/2 rounded-full opacity-55 blur-[1px] sm:h-72 sm:w-72"
      />
      {/* A soft scrim directly behind the headline, so the chrome gradient
          always has a dark ground to sit on whatever the sun is doing. */}
      <div
        aria-hidden
        className="absolute inset-x-0 top-24 -z-10 h-56 bg-[radial-gradient(ellipse_at_center,rgba(10,7,24,0.85)_0%,transparent_72%)]"
      />
      <Container className="relative flex flex-col items-center pt-24 pb-14 text-center sm:pt-32 sm:pb-16">
        <p className="inscription mb-5 text-[0.66rem] text-cyan">Est. MMXXVI</p>

        <h1 className="inscription chrome-text max-w-3xl text-4xl leading-[1.15] drop-shadow-[0_2px_18px_rgba(10,7,24,0.9)] sm:text-6xl">
          Excellent choice
        </h1>

        <Meander className="mt-7 w-40" />

        <p className="mt-7 max-w-xl text-base text-muted sm:text-lg">
          This storefront is a template. The architecture is finished — accounts,
          catalogue, checkout, payments and analytics all work. The merchandise
          is up to you.
        </p>

        <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
          <ButtonLink href="/shop">Enter the shop</ButtonLink>
          <ButtonLink href="/about" tone="ghost">
            What this is
          </ButtonLink>
        </div>
      </Container>

      {/* The horizon.
          The grid is laid down first and the city is overlaid onto it, offset
          from the bottom so the towers stand *in* the lights rather than
          floating above them. The perspective transform concentrates the grid
          into a band at the very bottom of its box, so simply stacking the two
          left an obvious dead gap between the buildings and the first dots. */}
      <div className="relative">
        <div aria-hidden className="horizon-grid" />
        <div className="absolute inset-x-0 bottom-[30px]">
          <Skyline />
        </div>
      </div>
    </section>
  );
}
