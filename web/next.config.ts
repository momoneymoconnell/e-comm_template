import type { NextConfig } from "next";

/**
 * Next.js configuration.
 *
 * The security headers here complement the ones the API sets. The API's
 * responses are JSON and lock everything down; these apply to the HTML
 * documents the browser actually renders, which need a more nuanced policy.
 */
const config: NextConfig = {
  // Emits a minimal server bundle with only the files actually imported, so
  // the runtime image ships without node_modules. Cuts it from ~1GB to ~150MB.
  output: "standalone",

  reactStrictMode: true,

  // The header is a free disclosure of which framework and version you run,
  // which is the first thing an automated scanner looks for.
  poweredByHeader: false,

  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            // `payment=(self ...)` is required: Stripe Elements uses the
            // Payment Request API from an iframe, and denying it outright
            // silently removes Apple Pay and Google Pay from the checkout.
            value:
              "camera=(), microphone=(), geolocation=(), payment=(self https://js.stripe.com)",
          },
        ],
      },
    ];
  },
};

export default config;
