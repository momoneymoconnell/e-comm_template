"use client";

/**
 * Sign in.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";

import { errorMessage, useSession } from "@/components/session-provider";
import { AuthShell, AuthField } from "@/components/auth-shell";
import { Button, ErrorNote } from "@/components/ui";

function LoginInner() {
  const { signIn } = useSession();
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);

    const form = new FormData(event.currentTarget);
    try {
      await signIn(String(form.get("email") ?? ""), String(form.get("password") ?? ""));

      // Only same-origin relative paths are honoured. Redirecting to an
      // arbitrary `?next=` value is an open-redirect: an attacker sends
      // /login?next=https://evil.example, the user signs in on the real site,
      // and lands on a convincing fake asking them to "confirm" their password.
      const next = params.get("next");
      const safeNext = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
      router.push(safeNext);
      router.refresh();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthShell title="Sign in" kicker="Welcome back">
      <form onSubmit={submit} className="space-y-5">
        <AuthField name="email" label="Email" type="email" required autoComplete="email" />
        <AuthField
          name="password"
          label="Password"
          type="password"
          required
          autoComplete="current-password"
        />

        {error ? <ErrorNote>{error}</ErrorNote> : null}

        <Button type="submit" disabled={pending} className="w-full">
          {pending ? "Signing in…" : "Sign in"}
        </Button>
      </form>

      <div className="mt-6 space-y-2 text-center text-xs text-muted">
        <p>
          No account?{" "}
          <Link href="/register" className="text-cyan underline-offset-4 hover:underline">
            Create one
          </Link>
        </p>
        <p>
          <Link
            href="/forgot-password"
            className="text-faint underline-offset-4 hover:text-cyan hover:underline"
          >
            Forgot your password?
          </Link>
        </p>
      </div>
    </AuthShell>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginInner />
    </Suspense>
  );
}
