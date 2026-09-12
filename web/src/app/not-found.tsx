/** 404 page. */

import { ButtonLink, Container, Meander } from "@/components/ui";

export default function NotFound() {
  return (
    <Container className="py-28">
      <div className="mx-auto max-w-md text-center">
        <p className="inscription chrome-text text-5xl">CDIV</p>
        <Meander className="mx-auto mt-5 w-28" />
        <h1 className="inscription mt-6 text-lg text-marble">Nothing stands here</h1>
        <p className="mt-3 text-sm text-muted">
          The page you asked for does not exist, or is not published.
        </p>
        <div className="mt-8 flex justify-center gap-3">
          <ButtonLink href="/">Return home</ButtonLink>
          <ButtonLink href="/shop" tone="ghost">
            Shop
          </ButtonLink>
        </div>
      </div>
    </Container>
  );
}
