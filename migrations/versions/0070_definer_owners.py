"""Cross-tenant definer functions owned by NOLOGIN roles (security audit M1).

Revision ID: 0070
Revises: 0068
"""

from pathlib import Path

from alembic import op

revision = "0070"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Give the twelve functions their NOLOGIN owners."""
    op.execute(Path("migrations/0070_definer_owners.sql").read_text())


def downgrade() -> None:
    """Hand them back to the migration user."""
    op.execute(Path("migrations/0070_definer_owners.down.sql").read_text())
