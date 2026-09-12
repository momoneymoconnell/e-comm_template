"use client";

/**
 * Create an account.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { AuthField, AuthShell } from "@/components/auth-shell";
import { errorMessage, useSession } from "@/components/session-provider";
import { Button, ErrorNote } from "@/components/ui";
import { ApiError } from "@/lib/api";

export default function RegisterPage() {
  const { signUp } = useSession();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    setFieldErrors({});

    const form = new FormData(event.currentTarget);
    try {
      await signUp(
        String(form.get("email") ?? ""),
        String(form.get("password") ?? ""),
        String(form.get("fullName") ?? "") || undefined,
      );
      router.push("/");
      router.refresh();
    } catch (caught) {
      // Validation errors come back keyed by field, so they can be shown next
      // to the input that caused them rather than as one generic banner.
      if (caught instanceof ApiError && Object.keys(caught.fieldErrors).length > 0) {
        setFieldErrors(caught.fieldErrors);
      }
      setError(errorMessage(caught));
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthShell title="Create account" kicker="Join">
      <form onSubmit={submit} className="space-y-5">
        <AuthField name="fullName" label="Name (optional)" autoComplete="name" />
        <AuthField
          name="email"
          label="Email"
          type="email"
          required
          autoComplete="email"
          {...(fieldErrors.email ? { "aria-invalid": true } : {})}
        />
        {fieldErrors.email ? (
          <p className="-mt-3 text-xs text-danger">{fieldErrors.email}</p>
        ) : null}

        <AuthField
          name="password"
          label="Password"
          type="password"
          required
          minLength={12}
          autoComplete="new-password"
          hint="At least 12 characters. Length matters far more than symbols."
          {...(fieldErrors.password ? { "aria-invalid": true } : {})}
        />
        {fieldErrors.password ? (
          <p className="-mt-3 text-xs text-danger">{fieldErrors.password}</p>
        ) : null}

        {error ? <ErrorNote>{error}</ErrorNote> : null}

        <Button type="submit" disabled={pending} className="w-full">
          {pending ? "Creating…" : "Create account"}
        </Button>
      </form>

      <p className="mt-6 text-center text-xs text-muted">
        Already registered?{" "}
        <Link href="/login" className="text-cyan underline-offset-4 hover:underline">
          Sign in
        </Link>
      </p>
    </AuthShell>
  );
}
