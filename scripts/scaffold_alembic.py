#!/usr/bin/env python3
"""Generate the Alembic scaffold for a service from the shared templates.

Keeps every service's migration setup byte-identical apart from its name, so a
fix to `env.py` is applied once and re-generated, not copy-pasted six times.

Usage:
    python scripts/scaffold_alembic.py auth ecom_auth AuthSettings
"""

from __future__ import annotations

import sys
from pathlib import Path

TEMPLATES = Path(__file__).parent / "templates"


def main(service: str, package: str, settings_class: str) -> int:
    """Write `alembic.ini`, `migrations/env.py` and `script.py.mako`.

    Args:
        service: Directory name under `services/`, e.g. ``"auth"``.
        package: Python package name, e.g. ``"ecom_auth"``.
        settings_class: Settings class to import, e.g. ``"AuthSettings"``.

    Returns:
        A shell exit code.
    """
    root = Path("services") / service
    if not root.exists():
        print(f"error: {root} does not exist", file=sys.stderr)
        return 1

    (root / "migrations" / "versions").mkdir(parents=True, exist_ok=True)

    # Tokens are @@NAME@@ rather than {NAME}: the templates are real Python
    # containing dict literals and f-strings, and str.format would choke on
    # every brace in them.
    subs = {
        "@@SERVICE@@": service,
        "@@PACKAGE@@": package,
        "@@SETTINGS_CLASS@@": settings_class,
    }

    def render(template: Path) -> str:
        text = template.read_text()
        for token, value in subs.items():
            text = text.replace(token, value)
        return text

    (root / "alembic.ini").write_text(render(TEMPLATES / "alembic.ini.tmpl"))
    (root / "migrations" / "env.py").write_text(render(TEMPLATES / "alembic_env.py.tmpl"))

    (root / "migrations" / "script.py.mako").write_text(
        (TEMPLATES / "script.py.mako").read_text()
    )

    # Alembic ignores a versions directory that git has dropped for being
    # empty, producing a confusing "Path doesn't exist" on the first run.
    keep = root / "migrations" / "versions" / ".gitkeep"
    keep.touch()

    print(f"scaffolded alembic for {service}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
