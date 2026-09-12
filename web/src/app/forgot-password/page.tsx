"use client";

/**
 * Request a password reset link.
 *
 * The confirmation below is shown whether or not the address has an account —
 * deliberately, and matching what the API returns. An unauthenticated form
 * that reveals which emails are registered is a customer-list extraction tool.
 */

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { AuthField, AuthShell } from "@/components/auth-shell";
import { Button, ErrorNote } from "@/components/ui";
import { errorMessage } from "@/components/session-provider";
import { apiFetch } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [sent, setSent] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);

    const form = new FormData(event.currentTarget);
    try {
      await apiFetch<unknown>("/auth/password/forgot", {
        method: "POST",
        json: { email: String(form.get("email") ?? "") },
      });
      setSent(true);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setPending(false);
    }
  }

  if (sent) {
    return (
      <AuthShell title="Check your email" kicker="Reset requested">
        <p className="text-sm text-muted">
          If that address has an account, a reset link is on its way. It is valid
          for one hour and can be used once.
        </p>
        <p className="mt-5 text-center text-xs">
          <Link href="/login" className="text-cyan underline-offset-4 hover:underline">
            Back to sign in
          </Link>
        </p>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Reset password" kicker="Forgot it?">
      <form onSubmit={submit} className="space-y-5">
        <AuthField
          name="email"
          label="Email"
          type="email"
          required
          autoComplete="email"
          hint="We will send a link if this address has an account."
        />
        {error ? <ErrorNote>{error}</ErrorNote> : null}
        <Button type="submit" disabled={pending} className="w-full">
          {pending ? "Sending…" : "Send reset link"}
        </Button>
      </form>
    </AuthShell>
  );
}
