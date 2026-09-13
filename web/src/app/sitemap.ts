/**
 * sitemap.xml, generated from the live catalogue.
 *
 * Next serves this at /sitemap.xml. It matters more than it looks: a search
 * engine discovers product pages by crawling links, and a shop with deep
 * categories can leave pages undiscovered for weeks. A sitemap lists them
 * directly.
 *
 * Only active products appear, because the API only returns active products.
 */

import type { MetadataRoute } from "next";

import { serverFetch } from "@/lib/api";
import type { Page, Product } from "@/lib/types";

/** Rebuilt hourly rather than per request; a sitemap does not need to be live. */
export const revalidate = 3600;

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const staticPages: MetadataRoute.Sitemap = [
    { url: `${SITE_URL}/`, changeFrequency: "daily", priority: 1 },
    { url: `${SITE_URL}/shop`, changeFrequency: "daily", priority: 0.9 },
    { url: `${SITE_URL}/about`, changeFrequency: "monthly", priority: 0.4 },
    { url: `${SITE_URL}/privacy`, changeFrequency: "yearly", priority: 0.2 },
    { url: `${SITE_URL}/terms`, changeFrequency: "yearly", priority: 0.2 },
  ];

  // A failure here must not take the sitemap down entirely; serving the static
  // pages is better than serving a 500 to a crawler.
  const products = await serverFetch<Page<Product>>("/catalog/products?pageSize=100");

  const productPages: MetadataRoute.Sitemap = (products?.items ?? []).map((product) => ({
    url: `${SITE_URL}/shop/${product.slug}`,
    lastModified: new Date(product.createdAt),
    changeFrequency: "weekly" as const,
    priority: 0.8,
  }));

  return [...staticPages, ...productPages];
}
