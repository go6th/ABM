"""The 6th Library: publication counts for the operator view.

Revision ID: 0058
Revises: 0057
"""

from pathlib import Path

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the identifier-and-count-only lookup."""
    op.execute(Path("migrations/0058_library_counts.sql").read_text())


def downgrade() -> None:
    """Drop the lookup."""
    op.execute(Path("migrations/0058_library_counts.down.sql").read_text())
