/**
 * A placeholder "about" page.
 *
 * Kept honest rather than filled with lorem ipsum: it says what the template
 * is, so a stakeholder clicking around a fresh install is not misled into
 * thinking a business already exists here.
 */

import { Container, Meander, Panel, SectionTitle } from "@/components/ui";

export const metadata = { title: "About" };

const CAPABILITIES = [
  ["Accounts", "Argon2id passwords, rotating refresh tokens, admin allowlist."],
  ["Catalogue", "Products, variants, categories, inventory with race-safe reservation."],
  ["Checkout", "Server-side pricing, stock reservation, compensating rollback."],
  ["Payments", "Stripe intents and verified webhooks. No card data is ever stored."],
  ["Analytics", "Privacy-first traffic events and a dbt-built warehouse."],
  ["Admin", "Traffic, revenue, orders, customers and payments in one console."],
];

export default function AboutPage() {
  return (
    <Container className="py-16">
      <SectionTitle kicker="What this is">A template, not a shop</SectionTitle>

      <div className="max-w-2xl space-y-5 text-sm leading-relaxed text-muted">
        <p>
          This storefront is scaffolding. Every system behind it works — accounts,
          catalogue, cart, checkout, payments, email and analytics — but no
          business has been decided yet, so the shelves are stocked with
          placeholders.
        </p>
        <p>
          The backend is a set of small Python services behind a single gateway,
          with Postgres for transactions and DuckDB for analysis. The whole thing
          starts with one command and is ready to move to a cloud host unchanged.
        </p>
      </div>

      <Meander className="my-12 max-w-sm" />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {CAPABILITIES.map(([title, body]) => (
          <Panel key={title} className="p-5">
            <p className="inscription text-[0.68rem] text-cyan/80">{title}</p>
            <p className="mt-2.5 text-sm text-muted">{body}</p>
          </Panel>
        ))}
      </div>
    </Container>
  );
}
