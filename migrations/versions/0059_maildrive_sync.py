"""Mail and drive: the per-user connections due for a sync.

Revision ID: 0059
Revises: 0058
"""

from pathlib import Path

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the identifier-only lookup."""
    op.execute(Path("migrations/0059_maildrive_sync.sql").read_text())


def downgrade() -> None:
    """Drop the lookup."""
    op.execute(Path("migrations/0059_maildrive_sync.down.sql").read_text())
