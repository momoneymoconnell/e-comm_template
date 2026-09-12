"use client";

/**
 * Reports a page view on every client-side navigation.
 *
 * The App Router does not do a full page load between routes, so a one-off
 * script in the document would fire exactly once per session and undercount
 * everything. Watching the pathname catches each route change instead.
 */

import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { track } from "@/lib/analytics";

export function PageViewTracker() {
  const pathname = usePathname();

  useEffect(() => {
    track("page_view");
  }, [pathname]);

  return null;
}
