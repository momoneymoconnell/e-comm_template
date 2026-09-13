"use client";

/**
 * Redeem an email verification link.
 *
 * Unauthenticated: the link is usually opened on a phone rather than the
 * machine that signed up, and requiring a session here is a reliable way to
 * make people give up.
 */

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { AuthShell } from "@/components/auth-shell";
import { errorMessage } from "@/components/session-provider";
import { ButtonLink, ErrorNote, Skeleton } from "@/components/ui";
import { apiFetch } from "@/lib/api";

function VerifyInner() {
  const token = useSearchParams().get("token") ?? "";
  const [state, setState] = useState<"working" | "done" | "failed">("working");
  const [error, setError] = useState<string | null>(null);

  // Guards against React's development double-invoke and any re-render, so the
  // single-use token is never redeemed twice - the second attempt would fail
  // and show an error on a verification that actually succeeded.
  const attempted = useRef(false);

  useEffect(() => {
    if (!token || attempted.current) return;
    attempted.current = true;

    void (async () => {
      try {
        await apiFetch<unknown>("/auth/email/verify", {
          method: "POST",
          json: { token },
        });
        setState("done");
      } catch (caught) {
        setError(errorMessage(caught));
        setState("failed");
      }
    })();
  }, [token]);

  if (!token) {
    return (
      <AuthShell title="Invalid link" kicker="Confirm your email">
        <p className="text-sm text-muted">
          This link is missing its token. Request a new one from your account page.
        </p>
        <p className="mt-5 text-center text-xs">
          <Link href="/account" className="text-cyan underline-offset-4 hover:underline">
            Go to your account
          </Link>
        </p>
      </AuthShell>
    );
  }

  if (state === "working") {
    return (
      <AuthShell title="Confirming…" kicker="Email">
        <Skeleton className="h-16" />
      </AuthShell>
    );
  }

  if (state === "failed") {
    return (
      <AuthShell title="Could not confirm" kicker="Email">
        <ErrorNote>{error ?? "That link is invalid or has expired."}</ErrorNote>
        <p className="mt-5 text-center text-xs text-muted">
          Sign in and request a new link from your{" "}
          <Link href="/account" className="text-cyan underline-offset-4 hover:underline">
            account page
          </Link>
          .
        </p>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Email confirmed" kicker="Thank you">
      <p className="text-sm text-muted">
        Your address is confirmed. Receipts and order updates will reach you.
      </p>
      <div className="mt-6 flex justify-center gap-3">
        <ButtonLink href="/shop">Start shopping</ButtonLink>
        <ButtonLink href="/account" tone="ghost">
          My account
        </ButtonLink>
      </div>
    </AuthShell>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={null}>
      <VerifyInner />
    </Suspense>
  );
}
