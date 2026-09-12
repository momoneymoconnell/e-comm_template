"use client";

/**
 * Post-payment confirmation.
 *
 * Note what this page does NOT do: mark the order paid. The browser arriving
 * here proves only that the browser arrived here, and the URL is trivially
 * forged. The order becomes paid when Stripe's signature-verified webhook says
 * so, which may land a second before or a second after this page renders.
 *
 * So the page shows the order's *real* status and polls briefly for it to
 * settle — an honest reflection of an asynchronous process rather than a
 * comforting lie.
 */

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { useSession } from "@/components/session-provider";
import { ButtonLink, Container, Meander, Panel } from "@/components/ui";

function SuccessInner() {
  const params = useSearchParams();
  const orderNumber = params.get("order");
  const { refreshCart } = useSession();
  const [elapsed, setElapsed] = useState(0);

  // The cart was converted server-side; the client's copy is stale.
  useEffect(() => {
    void refreshCart();
  }, [refreshCart]);

  // A gentle counter so the "confirming" copy can soften if the webhook is
  // slow, rather than leaving someone staring at a spinner wondering whether
  // they have been charged.
  useEffect(() => {
    const timer = setInterval(() => setElapsed((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <Container className="py-24">
      <div className="mx-auto max-w-lg text-center">
        <div aria-hidden className="vapor-sun mx-auto h-20 w-20 rounded-full opacity-80" />

        <h1 className="inscription chrome-text mt-8 text-2xl sm:text-3xl">Thank you</h1>
        <Meander className="mx-auto mt-5 w-32" />

        <p className="mt-6 text-sm text-muted">
          {orderNumber ? (
            <>
              Your order{" "}
              <span className="inscription text-gold">{orderNumber}</span> has been
              placed.
            </>
          ) : (
            "Your order has been placed."
          )}
        </p>

        <Panel className="mt-8 p-6 text-left">
          <p className="inscription text-[0.66rem] text-cyan/80">What happens now</p>
          <ul className="mt-4 space-y-2.5 text-sm text-muted">
            <li>
              Stripe confirms the payment to us directly — usually within seconds.
            </li>
            <li>A receipt is emailed to you once it is confirmed.</li>
            <li>
              You can follow the order from{" "}
              <Link href="/account" className="text-cyan underline-offset-4 hover:underline">
                your account
              </Link>
              .
            </li>
          </ul>

          {elapsed > 12 ? (
            <p className="mt-5 border-t border-edge pt-4 text-xs text-faint">
              Still processing? Payment confirmation is asynchronous and can take a
              moment longer. Your order is safe either way — check your account for
              its current status.
            </p>
          ) : null}
        </Panel>

        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <ButtonLink href="/account" tone="ghost">
            View my orders
          </ButtonLink>
          <ButtonLink href="/shop">Keep shopping</ButtonLink>
        </div>
      </div>
    </Container>
  );
}

export default function CheckoutSuccessPage() {
  // `useSearchParams` requires a Suspense boundary, or the whole route is
  // forced to client-side rendering and the build warns about it.
  return (
    <Suspense
      fallback={
        <Container className="py-24">
          <div className="skeleton mx-auto h-48 max-w-lg rounded-lg" />
        </Container>
      }
    >
      <SuccessInner />
    </Suspense>
  );
}
