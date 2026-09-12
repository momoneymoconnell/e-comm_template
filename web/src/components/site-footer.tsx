/**
 * The site footer. A server component: no state, no JavaScript shipped.
 */

import Link from "next/link";

import { Container, Meander } from "./ui";

const SITE_NAME = process.env.NEXT_PUBLIC_SITE_NAME || "Atelier";

export function SiteFooter() {
  return (
    <footer className="relative mt-24 border-t border-edge bg-night">
      <Container className="py-12">
        <Meander className="mb-8" />
        <div className="grid gap-8 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <p className="inscription text-sm text-marble">{SITE_NAME}</p>
            <p className="mt-3 max-w-xs text-sm text-muted">
              A template storefront. Replace this copy once the business is decided.
            </p>
          </div>

          <FooterColumn
            title="Shop"
            links={[
              { href: "/shop", label: "All products" },
              { href: "/cart", label: "Cart" },
            ]}
          />
          <FooterColumn
            title="Account"
            links={[
              { href: "/login", label: "Sign in" },
              { href: "/register", label: "Create account" },
              { href: "/account", label: "Orders" },
            ]}
          />
          <FooterColumn
            title="Legal"
            links={[
              { href: "/privacy", label: "Privacy" },
              { href: "/terms", label: "Terms" },
            ]}
          />
        </div>

        <p className="mt-10 text-xs text-faint">
          © {new Date().getFullYear()} {SITE_NAME}. Built on a template.
        </p>
      </Container>
    </footer>
  );
}

function FooterColumn({
  title,
  links,
}: {
  title: string;
  links: { href: string; label: string }[];
}) {
  return (
    <div>
      <p className="inscription mb-3 text-[0.66rem] text-cyan/80">{title}</p>
      <ul className="space-y-2">
        {links.map((link) => (
          <li key={link.href}>
            <Link
              href={link.href}
              className="text-sm text-muted transition-colors hover:text-marble"
            >
              {link.label}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
