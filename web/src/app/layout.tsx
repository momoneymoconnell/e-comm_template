/**
 * Root layout: fonts, metadata, providers and the page shell.
 */

import type { Metadata, Viewport } from "next";
import { Cinzel, Space_Grotesk } from "next/font/google";

import { PageViewTracker } from "@/components/page-view-tracker";
import { SessionProvider } from "@/components/session-provider";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

import "./globals.css";

/**
 * Cinzel: a typeface drawn from Roman inscriptional capitals. Used for
 * headings and labels only — it has no lowercase worth reading at body size.
 *
 * `display: "swap"` shows the fallback immediately and swaps the webfont in
 * when it arrives. The default (`block`) hides the text for up to three
 * seconds, which on a slow connection is a blank page.
 */
const cinzel = Cinzel({
  subsets: ["latin"],
  weight: ["400", "600", "700"],
  variable: "--font-cinzel",
  display: "swap",
});

/** Space Grotesk: a geometric sans with enough character for the aesthetic. */
const grotesk = Space_Grotesk({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600"],
  variable: "--font-grotesk",
  display: "swap",
});

const SITE_NAME = process.env.NEXT_PUBLIC_SITE_NAME || "Atelier";

export const metadata: Metadata = {
  title: {
    default: SITE_NAME,
    template: `%s · ${SITE_NAME}`,
  },
  description: "A full-stack e-commerce template.",
  // Kept out of search results until there is a real business behind it.
  // Flipped by NEXT_PUBLIC_ALLOW_INDEXING, the same switch robots.ts reads, so
  // launching does not mean remembering to edit two files.
  robots:
    process.env.NEXT_PUBLIC_ALLOW_INDEXING === "true"
      ? { index: true, follow: true }
      : { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Deliberately NOT setting maximumScale or userScalable. Blocking pinch-zoom
  // is a common "polish" that makes a site unusable for anyone with low vision.
  themeColor: "#0a0718",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${cinzel.variable} ${grotesk.variable}`}>
      <body className="scanlines flex min-h-dvh flex-col">
        {/* Lets a keyboard user jump past the navigation. Visible only on
            focus, which is exactly when it is needed. */}
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[200] focus:rounded-md focus:bg-neon focus:px-4 focus:py-2 focus:text-void"
        >
          Skip to content
        </a>

        <SessionProvider>
          <PageViewTracker />
          <SiteHeader />
          <main id="main" className="flex-1">
            {children}
          </main>
          <SiteFooter />
        </SessionProvider>
      </body>
    </html>
  );
}
