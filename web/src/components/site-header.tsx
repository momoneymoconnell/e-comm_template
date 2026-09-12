"use client";

/**
 * The site header: wordmark, navigation, cart and account.
 *
 * A client component because it reads session state and owns the mobile menu.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { useSession } from "./session-provider";

const NAV = [
  { href: "/", label: "Home" },
  { href: "/shop", label: "Shop" },
  { href: "/about", label: "About" },
];

const SITE_NAME = process.env.NEXT_PUBLIC_SITE_NAME || "Atelier";

export function SiteHeader() {
  const { user, cart } = useSession();
  const pathname = usePathname();
  // Closed from the link's own click handler rather than from an effect
  // watching the pathname. Same result, one fewer render, and it also closes
  // correctly when a link points at the page you are already on — which a
  // pathname watcher misses entirely.
  const [menuOpen, setMenuOpen] = useState(false);

  const itemCount = cart?.itemCount ?? 0;

  return (
    <header className="sticky top-0 z-50 border-b border-edge bg-void/85 backdrop-blur-md">
      <div className="mx-auto flex w-full max-w-6xl items-center gap-4 px-5 py-4 sm:px-8 lg:px-10">
        <Link href="/" className="group flex items-center gap-3" aria-label={`${SITE_NAME} home`}>
          {/* A tiny fluted column as the mark. Decorative, so hidden from AT. */}
          <span
            aria-hidden
            className="fluted h-7 w-5 rounded-sm border border-edge-bright bg-panel"
          />
          <span className="inscription text-sm text-marble transition-colors group-hover:text-neon sm:text-base">
            {SITE_NAME}
          </span>
        </Link>

        <nav className="ml-4 hidden items-center gap-7 md:flex" aria-label="Main">
          {NAV.map((item) => {
            const active =
              item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`inscription text-[0.68rem] transition-colors ${
                  active ? "text-neon" : "text-muted hover:text-cyan"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          {user?.role === "admin" ? (
            <Link
              href="/admin"
              className="inscription hidden text-[0.66rem] text-gold transition-colors hover:text-marble sm:inline"
            >
              Admin
            </Link>
          ) : null}

          <Link
            href={user ? "/account" : "/login"}
            className="inscription text-[0.66rem] text-muted transition-colors hover:text-cyan"
          >
            {user ? "Account" : "Sign in"}
          </Link>

          <Link
            href="/cart"
            className="relative inline-flex items-center gap-2 rounded-md border border-edge-bright px-3 py-2 transition-colors hover:border-neon"
            aria-label={`Cart, ${itemCount} item${itemCount === 1 ? "" : "s"}`}
          >
            <CartGlyph />
            {itemCount > 0 ? (
              <span className="absolute -right-1.5 -top-1.5 grid h-5 min-w-5 place-items-center rounded-full bg-neon px-1 text-[0.65rem] font-semibold text-void">
                {itemCount > 99 ? "99+" : itemCount}
              </span>
            ) : null}
          </Link>

          <button
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            aria-expanded={menuOpen}
            aria-controls="mobile-nav"
            aria-label="Toggle navigation"
            className="rounded-md border border-edge-bright p-2 text-muted transition-colors hover:border-cyan hover:text-cyan md:hidden"
          >
            <MenuGlyph open={menuOpen} />
          </button>
        </div>
      </div>

      {menuOpen ? (
        <nav
          id="mobile-nav"
          aria-label="Main"
          className="border-t border-edge bg-night px-5 py-3 md:hidden"
        >
          <ul className="flex flex-col">
            {NAV.map((item) => (
              <li key={item.href}>
                <Link
                  href={item.href}
                  onClick={() => setMenuOpen(false)}
                  className="inscription block py-3 text-[0.7rem] text-muted transition-colors hover:text-neon"
                >
                  {item.label}
                </Link>
              </li>
            ))}
            {user?.role === "admin" ? (
              <li>
                <Link
                  href="/admin"
                  onClick={() => setMenuOpen(false)}
                  className="inscription block py-3 text-[0.7rem] text-gold transition-colors hover:text-marble"
                >
                  Admin
                </Link>
              </li>
            ) : null}
          </ul>
        </nav>
      ) : null}
    </header>
  );
}

/** Cart icon. `aria-hidden` because the link already has a label. */
function CartGlyph() {
  return (
    <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M3 4h2l2.4 11.2a1 1 0 0 0 1 .8h8.8a1 1 0 0 0 1-.8L20 8H6" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="10" cy="19" r="1.3" />
      <circle cx="17" cy="19" r="1.3" />
    </svg>
  );
}

/** Hamburger / close icon. */
function MenuGlyph({ open }: { open: boolean }) {
  return (
    <svg aria-hidden viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8">
      {open ? (
        <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
      ) : (
        <path d="M4 7h16M4 12h16M4 17h16" strokeLinecap="round" />
      )}
    </svg>
  );
}
