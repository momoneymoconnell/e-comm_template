/**
 * Product listing, with category filter, search and pagination.
 *
 * A server component reading its state from the URL rather than from React
 * state. That makes every view shareable, bookmarkable and navigable with the
 * back button — all of which client-side filter state quietly breaks.
 */

import { headers } from "next/headers";
import Link from "next/link";

import { ProductCard } from "@/components/product-card";
import { Container, EmptyState, SectionTitle } from "@/components/ui";
import { serverFetch } from "@/lib/api";
import type { Category, Page, Product } from "@/lib/types";

export const metadata = { title: "Shop" };

const PAGE_SIZE = 12;

interface SearchParams {
  category?: string;
  q?: string;
  page?: string;
}

export default async function ShopPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await searchParams;
  const cookieHeader = (await headers()).get("cookie") ?? undefined;

  // Clamped rather than trusted. `?page=-5` or `?page=abc` from a crawler must
  // not reach the API as a malformed offset.
  const page = Math.max(1, Number.parseInt(params.page ?? "1", 10) || 1);

  const query = new URLSearchParams({ page: String(page), pageSize: String(PAGE_SIZE) });
  if (params.category) query.set("category", params.category);
  if (params.q) query.set("search", params.q);

  const [products, categories] = await Promise.all([
    serverFetch<Page<Product>>(`/catalog/products?${query}`, cookieHeader),
    serverFetch<Category[]>("/catalog/categories", cookieHeader),
  ]);

  const totalPages = products ? Math.max(1, Math.ceil(products.total / PAGE_SIZE)) : 1;

  return (
    <Container className="py-16">
      <SectionTitle kicker="Catalogue">
        {params.category
          ? (categories?.find((c) => c.slug === params.category)?.name ?? "Shop")
          : "Everything"}
      </SectionTitle>

      <form action="/shop" method="get" className="mb-8 flex flex-wrap gap-3">
        {params.category ? (
          <input type="hidden" name="category" value={params.category} />
        ) : null}
        <input
          type="search"
          name="q"
          defaultValue={params.q ?? ""}
          placeholder="Search the catalogue"
          aria-label="Search the catalogue"
          className="min-w-0 flex-1 rounded-md border border-edge bg-night px-4 py-2.5 text-sm text-ink placeholder:text-faint focus:border-cyan focus:outline-none"
        />
        <button
          type="submit"
          className="inscription rounded-md border border-edge-bright px-5 py-2.5 text-[0.68rem] text-ink transition-colors hover:border-cyan hover:text-cyan"
        >
          Search
        </button>
      </form>

      {categories && categories.length > 0 ? (
        <nav aria-label="Categories" className="mb-10 flex flex-wrap gap-2">
          <FilterChip href="/shop" active={!params.category}>
            All
          </FilterChip>
          {categories.map((category) => (
            <FilterChip
              key={category.id}
              href={`/shop?category=${encodeURIComponent(category.slug)}`}
              active={params.category === category.slug}
            >
              {category.name}
            </FilterChip>
          ))}
        </nav>
      ) : null}

      {products && products.items.length > 0 ? (
        <>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {products.items.map((product) => (
              <ProductCard key={product.id} product={product} />
            ))}
          </div>

          {totalPages > 1 ? (
            <Pagination
              page={page}
              totalPages={totalPages}
              category={params.category}
              q={params.q}
            />
          ) : null}
        </>
      ) : (
        <EmptyState title={params.q ? "No matches" : "Nothing here yet"}>
          {params.q
            ? `Nothing matched “${params.q}”. Try a different search.`
            : "The catalogue is empty. Run `make seed` for placeholder products, or add your own from the admin console."}
        </EmptyState>
      )}
    </Container>
  );
}

function FilterChip({
  href,
  active,
  children,
}: {
  href: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "true" : undefined}
      className={`inscription rounded-full border px-4 py-1.5 text-[0.62rem] transition-colors ${
        active
          ? "border-neon bg-neon/10 text-neon"
          : "border-edge text-muted hover:border-cyan hover:text-cyan"
      }`}
    >
      {children}
    </Link>
  );
}

function Pagination({
  page,
  totalPages,
  category,
  q,
}: {
  page: number;
  totalPages: number;
  category?: string;
  q?: string;
}) {
  const buildHref = (target: number) => {
    const query = new URLSearchParams({ page: String(target) });
    if (category) query.set("category", category);
    if (q) query.set("q", q);
    return `/shop?${query}`;
  };

  return (
    <nav aria-label="Pagination" className="mt-12 flex items-center justify-center gap-4">
      {page > 1 ? (
        <Link
          href={buildHref(page - 1)}
          rel="prev"
          className="inscription rounded-md border border-edge-bright px-4 py-2 text-[0.66rem] text-ink transition-colors hover:border-cyan hover:text-cyan"
        >
          ← Previous
        </Link>
      ) : (
        <span className="inscription rounded-md border border-edge px-4 py-2 text-[0.66rem] text-faint">
          ← Previous
        </span>
      )}

      <span className="text-xs text-muted">
        Page {page} of {totalPages}
      </span>

      {page < totalPages ? (
        <Link
          href={buildHref(page + 1)}
          rel="next"
          className="inscription rounded-md border border-edge-bright px-4 py-2 text-[0.66rem] text-ink transition-colors hover:border-cyan hover:text-cyan"
        >
          Next →
        </Link>
      ) : (
        <span className="inscription rounded-md border border-edge px-4 py-2 text-[0.66rem] text-faint">
          Next →
        </span>
      )}
    </nav>
  );
}
