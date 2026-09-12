"use client";

/**
 * Redeem a password reset link.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";

import { AuthField, AuthShell } from "@/components/auth-shell";
import { errorMessage } from "@/components/session-provider";
import { Button, ErrorNote } from "@/components/ui";
import { apiFetch } from "@/lib/api";

function ResetInner() {
  const params = useSearchParams();
  const router = useRouter();
  const token = params.get("token") ?? "";

  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);

    const form = new FormData(event.currentTarget);
    try {
      await apiFetch<unknown>("/auth/password/reset", {
        method: "POST",
        json: { token, newPassword: String(form.get("password") ?? "") },
      });
      setDone(true);
      setTimeout(() => router.push("/login"), 2200);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setPending(false);
    }
  }

  if (!token) {
    return (
      <AuthShell title="Invalid link" kicker="Reset password">
        <p className="text-sm text-muted">
          This link is missing its token. Request a new one.
        </p>
        <p className="mt-5 text-center text-xs">
          <Link
            href="/forgot-password"
            className="text-cyan underline-offset-4 hover:underline"
          >
            Request a reset link
          </Link>
        </p>
      </AuthShell>
    );
  }

  if (done) {
    return (
      <AuthShell title="Password updated" kicker="Done">
        <p className="text-sm text-muted">
          You can sign in with your new password. Taking you there now…
        </p>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Choose a new password" kicker="Reset password">
      <form onSubmit={submit} className="space-y-5">
        <AuthField
          name="password"
          label="New password"
          type="password"
          required
          minLength={12}
          autoComplete="new-password"
          hint="At least 12 characters."
        />
        {error ? <ErrorNote>{error}</ErrorNote> : null}
        <Button type="submit" disabled={pending} className="w-full">
          {pending ? "Updating…" : "Update password"}
        </Button>
      </form>
    </AuthShell>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetInner />
    </Suspense>
  );
}
