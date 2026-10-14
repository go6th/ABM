"""Private workspaces: tenant AND owner on every user-content table.

Revision ID: 0063
Revises: 0062
"""

from pathlib import Path

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add owners, backfill them and switch the policies."""
    op.execute(Path("migrations/0063_private_workspaces.sql").read_text())


def downgrade() -> None:
    """Back to tenant-only policies."""
    op.execute(Path("migrations/0063_private_workspaces.down.sql").read_text())
