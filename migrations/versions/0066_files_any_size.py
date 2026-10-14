"""Files of any size: parts, 64-bit passage numbers, the per-file limit.

Revision ID: 0066
Revises: 0065
"""

from pathlib import Path

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the parts and the per-file limit."""
    op.execute(Path("migrations/0066_files_any_size.sql").read_text())


def downgrade() -> None:
    """Remove them."""
    op.execute(Path("migrations/0066_files_any_size.down.sql").read_text())
