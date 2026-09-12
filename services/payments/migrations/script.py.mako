"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Created: ${create_date}

Checklist before merging a migration:
  * Is `downgrade()` correct? You will want it during an incident.
  * Does it lock a large table? ALTER TABLE ... ADD COLUMN with a non-null
    default rewrites the whole table on older Postgres; add the column
    nullable, backfill in batches, then set NOT NULL.
  * Does it drop or rename anything the running code still reads? Deploy the
    code change first, the schema change second.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """Apply this migration."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Revert this migration."""
    ${downgrades if downgrades else "pass"}
