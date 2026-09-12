/**
 * Shared frame and field for the auth pages, so sign-in, sign-up and password
 * reset cannot drift apart visually.
 */

import type { ReactNode } from "react";

import { Container, Meander, Panel } from "./ui";

export function AuthShell({
  title,
  kicker,
  children,
}: {
  title: string;
  kicker?: string;
  children: ReactNode;
}) {
  return (
    <Container className="py-20">
      <div className="mx-auto max-w-md">
        <div className="mb-8 text-center">
          {kicker ? (
            <p className="inscription mb-2 text-[0.64rem] text-cyan/80">{kicker}</p>
          ) : null}
          <h1 className="inscription text-xl text-marble">{title}</h1>
          <Meander className="mx-auto mt-4 w-24" />
        </div>

        <Panel className="p-7">{children}</Panel>
      </div>
    </Container>
  );
}

export function AuthField({
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
      <label htmlFor={name} className="inscription mb-2 block text-[0.64rem] text-cyan/80">
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
