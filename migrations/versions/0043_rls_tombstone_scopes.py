"""Separate tenant and platform tombstones; enforce forced RLS on newer tenant tables.

Revision ID: 0043
Revises: 0042
"""

from pathlib import Path

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path("migrations/0043_rls_tombstone_scopes.sql").read_text())


def downgrade() -> None:
    op.execute(Path("migrations/0043_rls_tombstone_scopes.down.sql").read_text())
