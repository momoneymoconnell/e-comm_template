/**
 * Privacy page.
 *
 * Describes what this template actually does with data, accurately. It is a
 * starting point, not legal advice — a real shop needs this reviewed against
 * its own jurisdiction and practices before launch.
 */

import { Container, Meander, SectionTitle } from "@/components/ui";

export const metadata = { title: "Privacy" };

export default function PrivacyPage() {
  return (
    <Container className="py-16">
      <SectionTitle kicker="Placeholder">Privacy</SectionTitle>

      <div className="max-w-2xl space-y-6 text-sm leading-relaxed text-muted">
        <p className="rounded-md border border-warn/40 bg-warn/10 px-4 py-3 text-warn">
          Draft text describing what the software does. Have it reviewed for your
          jurisdiction before you launch.
        </p>

        <Section title="What we store">
          Your email, name and shipping address when you order; an Argon2id hash
          of your password, never the password itself; and your order history.
        </Section>

        <Section title="What we never store">
          Card numbers, expiry dates and security codes. These go from your browser
          directly to Stripe. Our servers only ever see an opaque payment reference.
        </Section>

        <Section title="Analytics">
          We record page views to understand how the shop is used. We do not store
          your IP address or your full browser identity — both are reduced to a
          salted hash whose salt changes daily, so the record cannot be traced back
          to you or followed over time. Query strings are stripped before storage.
          If your browser sends a Do Not Track signal, nothing is recorded at all.
        </Section>

        <Section title="Retention">
          Traffic events are deleted after around 13 months; aggregate statistics,
          which identify nobody, are kept. Order records are retained as long as tax
          law requires.
        </Section>

        <Section title="Your rights">
          You can request a copy of your data or ask for your account to be deleted.
          Deleting an account removes your profile and sessions; order records are
          retained where law requires, with personal details minimised.
        </Section>
      </div>
    </Container>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h2 className="inscription mb-2 text-[0.7rem] text-cyan/80">{title}</h2>
      <Meander className="mb-3 max-w-[5rem]" />
      <p>{children}</p>
    </div>
  );
}
