"use client";

/**
 * Admin shell: the guard and the navigation.
 *
 * **This guard is convenience, not security.** It hides the UI from someone
 * who is not an admin; it does not protect the data. Every admin endpoint
 * independently verifies the caller's JWT and role server-side, so a curious
 * customer editing their local state, or calling the API directly with curl,
 * gets a 403 regardless of what this component renders.
 *
 * Treating a client-side check as the control is the classic way an admin
 * panel gets compromised.
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { useSession } from "@/components/session-provider";
import { Container, Meander, Skeleton } from "@/components/ui";

const ADMIN_NAV = [
  { href: "/admin", label: "Overview", exact: true },
  { href: "/admin/orders", label: "Orders" },
  { href: "/admin/products", label: "Catalogue" },
  { href: "/admin/customers", label: "Customers" },
  { href: "/admin/payments", label: "Payments" },
  { href: "/admin/traffic", label: "Traffic" },
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useSession();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    } else if (user.role !== "admin") {
      // Home, not an error page. A customer who wanders here does not need a
      // message explaining that an admin area exists.
      router.replace("/");
    }
  }, [loading, user, pathname, router]);

  if (loading || !user || user.role !== "admin") {
    return (
      <Container className="py-16">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="mt-6 h-48" />
      </Container>
    );
  }

  return (
    <Container className="py-12">
      <div className="mb-8">
        <p className="inscription text-[0.64rem] text-gold">Administration</p>
        <h1 className="inscription mt-2 text-xl text-marble">Console</h1>
        <Meander className="mt-3 max-w-[9rem]" />
      </div>

      <nav
        aria-label="Admin sections"
        className="mb-10 flex gap-1 overflow-x-auto border-b border-edge pb-px"
      >
        {ADMIN_NAV.map((item) => {
          const active = item.exact
            ? pathname === item.href
            : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={`inscription whitespace-nowrap border-b-2 px-4 py-2.5 text-[0.64rem] transition-colors ${
                active
                  ? "border-neon text-neon"
                  : "border-transparent text-muted hover:text-cyan"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>

      {children}
    </Container>
  );
}
