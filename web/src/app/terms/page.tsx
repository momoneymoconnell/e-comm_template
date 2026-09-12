/** Terms placeholder. */

import { Container, SectionTitle } from "@/components/ui";

export const metadata = { title: "Terms" };

export default function TermsPage() {
  return (
    <Container className="py-16">
      <SectionTitle kicker="Placeholder">Terms</SectionTitle>
      <div className="max-w-2xl space-y-5 text-sm leading-relaxed text-muted">
        <p className="rounded-md border border-warn/40 bg-warn/10 px-4 py-3 text-warn">
          No terms have been written. Replace this page before selling anything.
        </p>
        <p>
          Terms of sale depend entirely on what you sell, where you sell it and to
          whom. This template deliberately ships an empty page rather than
          boilerplate that would be wrong for your business.
        </p>
      </div>
    </Container>
  );
}
