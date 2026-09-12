"use client";

/**
 * Route-level error boundary.
 *
 * Shows the digest rather than the message. Next redacts server error messages
 * in production and replaces them with a digest that correlates to the server
 * log — which is exactly right, since an exception message can leak internals.
 */

import { useEffect } from "react";

import { Button, ButtonLink, Container, Meander } from "@/components/ui";

export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Unhandled route error:", error);
  }, [error]);

  return (
    <Container className="py-28">
      <div className="mx-auto max-w-md text-center">
        <p className="inscription chrome-text text-4xl">Ruina</p>
        <Meander className="mx-auto mt-5 w-28" />
        <h1 className="inscription mt-6 text-lg text-marble">Something collapsed</h1>
        <p className="mt-3 text-sm text-muted">
          An unexpected error occurred. Trying again often resolves it.
        </p>
        {error.digest ? (
          <p className="mt-4 font-mono text-xs text-faint">Reference: {error.digest}</p>
        ) : null}
        <div className="mt-8 flex justify-center gap-3">
          <Button onClick={reset}>Try again</Button>
          <ButtonLink href="/" tone="ghost">
            Home
          </ButtonLink>
        </div>
      </div>
    </Container>
  );
}
