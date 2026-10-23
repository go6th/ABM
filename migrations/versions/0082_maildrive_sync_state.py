"""Mail and drive sync state: last status, error code and counts per connection (2026-10-23).

Revision ID: 0082
Revises: 0081
"""

from pathlib import Path

from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the sync state columns."""
    sql = Path(__file__).resolve().parents[1] / "0082_maildrive_sync_state.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop the sync state columns."""
    sql = Path(__file__).resolve().parents[1] / "0082_maildrive_sync_state.down.sql"
    op.execute(sql.read_text())
