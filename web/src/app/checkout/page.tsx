"use client";

/**
 * Checkout.
 *
 * Two stages in one page:
 *
 * 1. Collect the email and shipping address, then POST to `/orders/checkout`.
 *    The server prices the cart from the catalogue, reserves stock, creates the
 *    order and returns a Stripe client secret. **No amount is sent from here** —
 *    a checkout that accepted a total would let anyone buy anything for a penny.
 * 2. Mount Stripe Elements with that secret and confirm the payment.
 *
 * Card details go from the iframe straight to Stripe. They never touch this
 * component, this app's memory, or our servers — which is what keeps the whole
 * system out of PCI-DSS scope.
 */

import { Elements, PaymentElement, useElements, useStripe } from "@stripe/react-stripe-js";
import { loadStripe, type Stripe } from "@stripe/stripe-js";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { errorMessage, useSession } from "@/components/session-provider";
import { Button, Container, ErrorNote, Meander, Panel, SectionTitle } from "@/components/ui";
import { track } from "@/lib/analytics";
import { apiFetch } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import type { Address, CheckoutResult } from "@/lib/types";

/**
 * The publishable key is inlined at build time and is public by design — it
 * identifies the account and can only create payment methods, never read or
 * move money. The secret key never leaves the payments service.
 */
const PUBLISHABLE_KEY = process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY ?? "";

/**
 * `loadStripe` is called once at module scope, not per render.
 *
 * It injects a script tag and returns a promise. Calling it inside the
 * component would re-run on every render and re-initialise Stripe, which
 * remounts Elements and wipes whatever the customer has typed.
 */
const stripePromise: Promise<Stripe | null> | null = PUBLISHABLE_KEY
  ? loadStripe(PUBLISHABLE_KEY)
  : null;

export default function CheckoutPage() {
  const { cart, user, loading } = useSession();
  const router = useRouter();

  const [checkout, setCheckout] = useState<CheckoutResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    track("checkout_started");
  }, []);

  // Send an empty cart back to the shop, but only once loading has settled —
  // redirecting while the cart is still being fetched would bounce every
  // customer off the page they just clicked to.
  useEffect(() => {
    if (!loading && cart && cart.items.length === 0 && !checkout) {
      router.replace("/cart");
    }
  }, [loading, cart, checkout, router]);

  async function startCheckout(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    const form = new FormData(event.currentTarget);
    const address: Address = {
      fullName: String(form.get("fullName") ?? ""),
      line1: String(form.get("line1") ?? ""),
      line2: String(form.get("line2") ?? "") || null,
      city: String(form.get("city") ?? ""),
      region: String(form.get("region") ?? "") || null,
      postalCode: String(form.get("postalCode") ?? ""),
      country: String(form.get("country") ?? "US"),
      phone: String(form.get("phone") ?? "") || null,
    };

    try {
      const result = await apiFetch<CheckoutResult>("/orders/checkout", {
        method: "POST",
        json: {
          email: String(form.get("email") ?? ""),
          shippingAddress: address,
        },
      });
      setCheckout(result);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  const elementsOptions = useMemo(
    () =>
      checkout
        ? {
            clientSecret: checkout.clientSecret,
            appearance: {
              theme: "night" as const,
              variables: {
                colorPrimary: "#ff5fd2",
                colorBackground: "#150f2e",
                colorText: "#e6dffb",
                colorDanger: "#ff7a8a",
                borderRadius: "8px",
                fontFamily: "system-ui, sans-serif",
              },
            },
          }
        : null,
    [checkout],
  );

  if (!PUBLISHABLE_KEY) {
    return (
      <Container className="py-16">
        <SectionTitle kicker="Checkout">Payment not configured</SectionTitle>
        <Panel className="p-6">
          <p className="text-sm text-muted">
            Set <code className="rounded bg-void px-1.5 py-0.5 font-mono text-cyan">
              NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY
            </code>{" "}
            and{" "}
            <code className="rounded bg-void px-1.5 py-0.5 font-mono text-cyan">
              STRIPE_SECRET_KEY
            </code>{" "}
            in <code className="font-mono text-cyan">.env</code>, then rebuild.
            Test keys are at dashboard.stripe.com/test/apikeys.
          </p>
        </Panel>
      </Container>
    );
  }

  return (
    <Container className="py-16">
      <SectionTitle kicker="Final step">Checkout</SectionTitle>

      <div className="grid gap-10 lg:grid-cols-[1fr_20rem]">
        <div>
          {error ? (
            <div className="mb-6">
              <ErrorNote>{error}</ErrorNote>
            </div>
          ) : null}

          {!checkout ? (
            <form onSubmit={startCheckout} className="space-y-5">
              <Field
                name="email"
                label="Email"
                type="email"
                required
                defaultValue={user?.email ?? ""}
                autoComplete="email"
                hint="Your receipt and order updates go here."
              />

              <Meander className="max-w-[6rem]" />

              <Field name="fullName" label="Full name" required autoComplete="name" />
              <Field name="line1" label="Address" required autoComplete="address-line1" />
              <Field name="line2" label="Address line 2" autoComplete="address-line2" />

              <div className="grid gap-5 sm:grid-cols-2">
                <Field name="city" label="City" required autoComplete="address-level2" />
                <Field
                  name="region"
                  label="State / province"
                  autoComplete="address-level1"
                />
              </div>

              <div className="grid gap-5 sm:grid-cols-2">
                <Field
                  name="postalCode"
                  label="Postal code"
                  required
                  autoComplete="postal-code"
                />
                <Field
                  name="country"
                  label="Country"
                  required
                  defaultValue="US"
                  maxLength={2}
                  autoComplete="country"
                  hint="Two-letter code, e.g. US, GB, DE."
                />
              </div>

              <Field name="phone" label="Phone (optional)" autoComplete="tel" />

              <Button type="submit" disabled={submitting} className="w-full sm:w-auto">
                {submitting ? "Preparing…" : "Continue to payment"}
              </Button>
            </form>
          ) : (
            <Panel className="p-6">
              <p className="inscription mb-1 text-[0.68rem] text-cyan/80">
                Order {checkout.orderNumber}
              </p>
              <p className="mb-6 text-sm text-muted">
                Card details go directly to Stripe and never reach our servers.
              </p>

              {stripePromise && elementsOptions ? (
                <Elements stripe={stripePromise} options={elementsOptions}>
                  <PaymentForm checkout={checkout} />
                </Elements>
              ) : null}
            </Panel>
          )}
        </div>

        {cart ? (
          <Panel className="h-fit p-6 lg:sticky lg:top-28">
            <p className="inscription text-[0.68rem] text-cyan/80">Order</p>
            <Meander className="my-4" />
            <ul className="space-y-2.5 text-sm">
              {cart.items.map((item) => (
                <li key={item.id} className="flex justify-between gap-3">
                  <span className="min-w-0 text-muted">
                    <span className="block truncate text-ink">{item.productTitle}</span>
                    <span className="text-xs">
                      {item.variantName} × {item.quantity}
                    </span>
                  </span>
                  <span className="shrink-0 text-ink">
                    {formatMoney(item.totalCents, cart.currency)}
                  </span>
                </li>
              ))}
            </ul>
            <Meander className="my-4" />
            <div className="flex items-baseline justify-between">
              <span className="inscription text-[0.7rem] text-marble">Total</span>
              <span className="inscription text-lg text-gold">
                {formatMoney(cart.totalCents, cart.currency)}
              </span>
            </div>
          </Panel>
        ) : null}
      </div>
    </Container>
  );
}

/**
 * The Stripe payment step.
 *
 * Split into its own component because `useStripe` and `useElements` only work
 * inside `<Elements>`.
 */
function PaymentForm({ checkout }: { checkout: CheckoutResult }) {
  const stripe = useStripe();
  const elements = useElements();
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm(event: FormEvent) {
    event.preventDefault();
    if (!stripe || !elements) return;

    setSubmitting(true);
    setError(null);

    const { error: stripeError } = await stripe.confirmPayment({
      elements,
      confirmParams: {
        // Where Stripe sends the customer after a redirect-based method
        // (3-D Secure, iDEAL, Klarna). Must be absolute.
        return_url: `${window.location.origin}/checkout/success?order=${checkout.orderNumber}`,
      },
      // Only redirect when the payment method actually requires it. Card
      // payments then complete in place, which is a noticeably better flow.
      redirect: "if_required",
    });

    if (stripeError) {
      // Stripe's customer-facing messages are written to be shown to shoppers,
      // so this is safe to render directly.
      setError(stripeError.message ?? "That payment could not be completed.");
      setSubmitting(false);
      return;
    }

    track("checkout_completed", { orderNumber: checkout.orderNumber });
    // The success page re-fetches the cart itself, so a client-side push is
    // enough and keeps the transition instant.
    router.push(`/checkout/success?order=${checkout.orderNumber}`);
  }

  return (
    <form onSubmit={confirm} className="space-y-5">
      <PaymentElement />
      {error ? <ErrorNote>{error}</ErrorNote> : null}
      <Button type="submit" disabled={!stripe || submitting} className="w-full">
        {submitting ? "Processing…" : `Pay ${formatMoney(checkout.totalCents, checkout.currency)}`}
      </Button>
      <p className="text-center text-xs text-faint">
        Payments are processed by Stripe. We never see your card details.
      </p>
    </form>
  );
}

/** A labelled text input. */
function Field({
  name,
  label,
  hint,
  ...props
}: {
  name: string;
  label: string;
  hint?: string;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  const hintId = hint ? `${name}-hint` : undefined;
  return (
    <div>
      <label
        htmlFor={name}
        className="inscription mb-2 block text-[0.64rem] text-cyan/80"
      >
        {label}
      </label>
      <input
        id={name}
        name={name}
        aria-describedby={hintId}
        className="w-full rounded-md border border-edge bg-night px-4 py-2.5 text-sm text-ink placeholder:text-faint focus:border-cyan focus:outline-none"
        {...props}
      />
      {hint ? (
        <p id={hintId} className="mt-1.5 text-xs text-faint">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
