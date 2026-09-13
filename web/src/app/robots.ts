/**
 * robots.txt, generated so the launch switch is one environment variable.
 *
 * This template ships closed to crawlers: it is a placeholder shop, and having
 * "Placeholder Item 01" indexed before launch is worse than not being indexed
 * at all. Set `NEXT_PUBLIC_ALLOW_INDEXING=true` when the real catalogue is live.
 *
 * Replaces the static public/robots.txt, which could only be changed by editing
 * a file and rebuilding.
 */

import type { MetadataRoute } from "next";

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";
const ALLOW_INDEXING = process.env.NEXT_PUBLIC_ALLOW_INDEXING === "true";

export default function robots(): MetadataRoute.Robots {
  if (!ALLOW_INDEXING) {
    return { rules: [{ userAgent: "*", disallow: "/" }] };
  }

  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        // Nothing here is secret - these pages are either per-customer or
        // meaningless to a crawler, and indexing them wastes crawl budget on
        // pages that will never rank.
        disallow: ["/admin", "/account", "/checkout", "/cart", "/reset-password"],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
  };
}
