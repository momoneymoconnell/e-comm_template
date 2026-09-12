/**
 * Shared presentational primitives.
 *
 * Server components by default — none of these need state or effects, so none
 * pays the cost of shipping JavaScript to the browser for it.
 */

import Link from "next/link";
import type { ReactNode } from "react";

/* --- Ornament ------------------------------------------------------------ */

/**
 * The Greek-key meander, used as a section divider.
 *
 * Decorative, so it is hidden from assistive technology: a screen reader
 * announcing a repeating pattern adds noise and no meaning.
 */
export function Meander({ className = "" }: { className?: string }) {
  return <div aria-hidden className={`meander ${className}`} />;
}

/**
 * A fluted vertical rule, echoing a column shaft.
 */
export function Fluting({ className = "" }: { className?: string }) {
  return <div aria-hidden className={`fluted w-full ${className}`} />;
}

/* --- Layout -------------------------------------------------------------- */

/**
 * Standard page container.
 *
 * The horizontal padding steps up at each breakpoint so text never runs into
 * the edge of a phone screen, and the max width keeps line length readable on
 * a wide monitor — past about 75 characters the eye loses its place returning
 * to the next line.
 */
export function Container({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`mx-auto w-full max-w-6xl px-5 sm:px-8 lg:px-10 ${className}`}>
      {children}
    </div>
  );
}

/**
 * A section heading in Roman inscriptional capitals, with a rule beneath.
 */
export function SectionTitle({
  children,
  kicker,
  className = "",
}: {
  children: ReactNode;
  kicker?: string;
  className?: string;
}) {
  return (
    <div className={`mb-8 ${className}`}>
      {kicker ? (
        <p className="inscription mb-2 text-[0.68rem] text-cyan/80">{kicker}</p>
      ) : null}
      <h2 className="inscription text-xl text-marble sm:text-2xl">{children}</h2>
      <Meander className="mt-3 max-w-[9rem]" />
    </div>
  );
}

/* --- Surfaces ------------------------------------------------------------ */

/**
 * A bordered panel. The building block of every card and table container.
 */
export function Panel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`surface ${className}`}>{children}</div>;
}

/* --- Controls ------------------------------------------------------------ */

type ButtonTone = "primary" | "ghost" | "danger";

const TONE_CLASSES: Record<ButtonTone, string> = {
  primary:
    "bg-neon text-void border-neon hover:bg-marble hover:border-marble " +
    "shadow-[0_0_24px_-8px_var(--color-neon)]",
  ghost: "bg-transparent text-ink border-edge-bright hover:border-cyan hover:text-cyan",
  danger: "bg-transparent text-danger border-danger/50 hover:bg-danger hover:text-void",
};

const BASE_BUTTON =
  "inscription inline-flex items-center justify-center gap-2 rounded-md border " +
  "px-5 py-2.5 text-[0.7rem] transition-colors duration-200 " +
  "disabled:cursor-not-allowed disabled:opacity-45";

/**
 * A button.
 *
 * A real `<button>`, not a styled div, so it is focusable, keyboard-activatable
 * and announced correctly — all of which a div silently is not.
 */
export function Button({
  children,
  tone = "primary",
  className = "",
  ...props
}: {
  children: ReactNode;
  tone?: ButtonTone;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button className={`${BASE_BUTTON} ${TONE_CLASSES[tone]} ${className}`} {...props}>
      {children}
    </button>
  );
}

/**
 * A link styled as a button. Uses `next/link` for client-side navigation.
 */
export function ButtonLink({
  children,
  href,
  tone = "primary",
  className = "",
}: {
  children: ReactNode;
  href: string;
  tone?: ButtonTone;
  className?: string;
}) {
  return (
    <Link href={href} className={`${BASE_BUTTON} ${TONE_CLASSES[tone]} ${className}`}>
      {children}
    </Link>
  );
}

/* --- Feedback ------------------------------------------------------------ */

/**
 * A small status pill.
 */
export function Pill({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-[0.62rem] " +
        `uppercase tracking-[0.16em] ${className}`
      }
    >
      {children}
    </span>
  );
}

/**
 * An error message.
 *
 * `role="alert"` makes a screen reader announce it as soon as it appears —
 * without it, a visually obvious failure is completely silent to someone not
 * looking at that part of the page.
 */
export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p
      role="alert"
      className="rounded-md border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger"
    >
      {children}
    </p>
  );
}

/**
 * An empty-state block, shown where a list has nothing in it.
 */
export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="surface flex flex-col items-center gap-3 px-6 py-14 text-center">
      <div aria-hidden className="vapor-sun h-12 w-12 rounded-full opacity-60" />
      <h3 className="inscription text-sm text-marble">{title}</h3>
      {children ? <p className="max-w-sm text-sm text-muted">{children}</p> : null}
      {action}
    </div>
  );
}

/**
 * A loading placeholder shaped roughly like the content it stands in for.
 */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden className={`skeleton rounded-md ${className}`} />;
}
