#!/usr/bin/env python3
"""Fill every placeholder secret in a local `.env` with strong random values.

Run via ``make secrets``. It rewrites `.env` in place, and only ever touches
keys whose value still starts with ``CHANGE_ME``. Anything you have already
filled in — your Stripe keys, your admin email — is left alone, so the command
is safe to run repeatedly.

Nothing here is printed to stdout except a summary of which keys changed. The
generated values themselves stay in the file, because a secret echoed to a
terminal ends up in your shell history.
"""

from __future__ import annotations

import secrets
import string
import sys
from pathlib import Path

#: Keys that need hex output. JWT/CSRF signing keys are compared byte-for-byte
#: and never typed by a human, so hex keeps them shell-safe and unambiguous.
HEX_KEYS = {"JWT_SECRET_KEY", "CSRF_SECRET_KEY"}

#: Keys that are database passwords. These end up inside connection strings and
#: inside SQL, so the alphabet deliberately excludes quotes, backslashes, `@`,
#: `:` and `/` — every character that would otherwise need escaping in a DSN.
PASSWORD_KEYS = {
    "POSTGRES_PASSWORD",
    "SVC_AUTH_DB_PASSWORD",
    "SVC_CATALOG_DB_PASSWORD",
    "SVC_ORDERS_DB_PASSWORD",
    "SVC_PAYMENTS_DB_PASSWORD",
    "SVC_ANALYTICS_DB_PASSWORD",
    "SVC_NOTIFICATIONS_DB_PASSWORD",
    "DBT_DB_PASSWORD",
}

#: The admin password is one you will actually type, so it is generated from a
#: slightly friendlier alphabet — still 20+ characters of real entropy.
HUMAN_KEYS = {"BOOTSTRAP_ADMIN_PASSWORD"}

DSN_SAFE_ALPHABET = string.ascii_letters + string.digits + "-._~"


def generate_for(key: str) -> str:
    """Return an appropriate random value for `key`.

    Args:
        key: The environment variable name.

    Returns:
        A generated secret sized and encoded for that key's use.
    """
    if key in HEX_KEYS:
        return secrets.token_hex(32)  # 64 hex chars = 256 bits
    if key in PASSWORD_KEYS:
        return "".join(secrets.choice(DSN_SAFE_ALPHABET) for _ in range(32))
    if key in HUMAN_KEYS:
        return "".join(secrets.choice(DSN_SAFE_ALPHABET) for _ in range(24))
    return secrets.token_urlsafe(32)


def main() -> int:
    """Rewrite `.env`, replacing placeholder values. Returns a shell exit code."""
    env_path = Path(".env")
    if not env_path.exists():
        print("error: .env not found. Run `cp .env.example .env` first.", file=sys.stderr)
        return 1

    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    changed: list[str] = []
    skipped_manual: list[str] = []

    for line in lines:
        stripped = line.strip()
        # Preserve comments and blank lines exactly as written.
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            updated.append(line)
            continue

        key, _, value = stripped.partition("=")
        key, value = key.strip(), value.strip()

        if not value.startswith("CHANGE_ME"):
            updated.append(line)
            continue

        # Values only you can supply. Generating a fake Stripe key would produce
        # a stack trace at checkout rather than a clear "you forgot this".
        if key.startswith("STRIPE_") or key.startswith("NEXT_PUBLIC_STRIPE"):
            skipped_manual.append(key)
            updated.append(line)
            continue

        updated.append(f"{key}={generate_for(key)}")
        changed.append(key)

    env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    # Owner-only. A .env readable by other local accounts is a real leak on a
    # shared machine, and this costs nothing to get right.
    env_path.chmod(0o600)

    if changed:
        print(f"Generated {len(changed)} secret(s):")
        for key in changed:
            print(f"  - {key}")
    else:
        print("No placeholder secrets left to generate.")

    if skipped_manual:
        print("\nStill needed from you (get them from dashboard.stripe.com/test/apikeys):")
        for key in skipped_manual:
            print(f"  - {key}")

    print("\n.env permissions set to 600 (owner read/write only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
