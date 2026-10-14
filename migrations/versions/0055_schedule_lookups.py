"""The scheduler's identifier-only lookups: due scheduled refreshes, tenants with waiting jobs.

Revision ID: 0055
Revises: 0054
"""

from pathlib import Path

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the two definer functions."""
    op.execute(Path("migrations/0055_schedule_lookups.sql").read_text())


def downgrade() -> None:
    """Drop them."""
    op.execute(Path("migrations/0055_schedule_lookups.down.sql").read_text())
