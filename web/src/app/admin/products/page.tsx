"use client";

/**
 * Catalogue management.
 *
 * Read and status changes only. Full product editing is a large form with
 * image handling and variant matrices; the API supports it
 * (POST/PATCH /catalog/admin/products), and this page is the place to build
 * it once the catalogue's actual shape is known.
 */

import Link from "next/link";
import { useState } from "react";

import { Stat, TableWrap, Td, Th } from "@/components/admin-ui";
import { Button, EmptyState, ErrorNote, Pill, Skeleton } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import { useAsyncData } from "@/lib/use-async";
import { formatMoney, formatNumber } from "@/lib/format";
import type { CatalogStats, Page, Product } from "@/lib/types";

const PAGE_SIZE = 25;

export default function AdminProductsPage() {
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const { data: result, error, loading } = useAsyncData<{
    products: Page<Product>;
    stats: CatalogStats;
  }>(async () => {
    const query = new URLSearchParams({
      page: String(page),
      pageSize: String(PAGE_SIZE),
    });
    if (status) query.set("status", status);
    if (search.trim()) query.set("search", search.trim());

    const [products, stats] = await Promise.all([
      apiFetch<Page<Product>>(`/catalog/admin/products?${query}`),
      apiFetch<CatalogStats>("/catalog/admin/stats"),
    ]);
    return { products, stats };
  }, [page, status, search]);

  const data = result?.products ?? null;
  const stats = result?.stats ?? null;

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Active"
          value={stats ? formatNumber(stats.activeProducts) : "—"}
          loading={loading}
        />
        <Stat
          label="Drafts"
          value={stats ? formatNumber(stats.draftProducts) : "—"}
          loading={loading}
        />
        <Stat
          label="Units in stock"
          value={stats ? formatNumber(stats.unitsInStock) : "—"}
          loading={loading}
        />
        <Stat
          label="Out of stock"
          value={stats ? formatNumber(stats.outOfStockVariants) : "—"}
          tone={stats && stats.outOfStockVariants > 0 ? "warn" : "default"}
          loading={loading}
        />
      </div>

      <div className="flex justify-end">
        <Link
          href="/admin/products/new"
          className="inscription inline-flex items-center rounded-md border border-neon bg-neon px-5 py-2.5 text-[0.7rem] text-void transition-colors hover:border-marble hover:bg-marble"
        >
          New product
        </Link>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-48 flex-1">
          <label htmlFor="product-search" className="inscription mb-2 block text-[0.58rem] text-cyan/70">
            Search
          </label>
          <input
            id="product-search"
            type="search"
            value={search}
            placeholder="Title or subtitle"
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
            className="w-full rounded-md border border-edge bg-night px-4 py-2 text-sm text-ink placeholder:text-faint focus:border-cyan focus:outline-none"
          />
        </div>
        <div>
          <label htmlFor="product-status" className="inscription mb-2 block text-[0.58rem] text-cyan/70">
            Status
          </label>
          <select
            id="product-status"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setPage(1);
            }}
            className="rounded-md border border-edge bg-night px-4 py-2 text-sm text-ink focus:border-cyan focus:outline-none"
          >
            <option value="">All</option>
            <option value="active">Active</option>
            <option value="draft">Draft</option>
            <option value="archived">Archived</option>
          </select>
        </div>
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
                <Th>Product</Th>
                <Th>Category</Th>
                <Th>Status</Th>
                <Th>Variants</Th>
                <Th className="text-right">From</Th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((product) => {
                const prices = product.variants.map((v) => v.priceCents);
                const from = prices.length > 0 ? Math.min(...prices) : null;
                const soldOut =
                  product.variants.length > 0 &&
                  product.variants.every((v) => !v.inStock);
                return (
                  <tr key={product.id} className="transition-colors hover:bg-raised/50">
                    <Td>
                      <Link
                        href={`/admin/products/${product.id}`}
                        className="text-sm text-marble transition-colors hover:text-neon"
                      >
                        {product.title}
                      </Link>
                      <span className="block font-mono text-xs text-faint">
                        {product.slug}
                      </span>
                    </Td>
                    <Td className="text-xs text-muted">{product.category?.name ?? "—"}</Td>
                    <Td>
                      <Pill
                        className={
                          product.status === "active"
                            ? "border-ok/40 text-ok"
                            : product.status === "draft"
                              ? "border-warn/40 text-warn"
                              : "border-edge text-faint"
                        }
                      >
                        {product.status}
                      </Pill>
                    </Td>
                    <Td className="text-xs">
                      {product.variants.length}
                      {soldOut ? (
                        <span className="ml-2 text-warn">all sold out</span>
                      ) : null}
                    </Td>
                    <Td className="whitespace-nowrap text-right text-gold">
                      {from !== null
                        ? formatMoney(from, product.variants[0]?.currency ?? "usd")
                        : "—"}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </TableWrap>

          <div className="flex items-center justify-between text-xs text-muted">
            <span>
              {data.total} product{data.total === 1 ? "" : "s"}
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
        <EmptyState title="No products">
          Run <code className="font-mono text-cyan">make seed</code> to load
          placeholder products, or create one with the button above.
        </EmptyState>
      )}
    </div>
  );
}
