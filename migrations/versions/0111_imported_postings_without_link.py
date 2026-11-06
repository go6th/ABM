"""A posting imported from an uploaded workspace may have no web address (2026-11-05, exact copy).

Revision ID: 0111
Revises: 0110
"""

from pathlib import Path

from alembic import op

revision = "0111"
down_revision = "0110"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Widen the posting address check for file-import rows."""
    sql = Path(__file__).resolve().parents[1] / "0111_imported_postings_without_link.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop the linkless imported rows and restore the strict check."""
    sql = Path(__file__).resolve().parents[1] / "0111_imported_postings_without_link.down.sql"
    op.execute(sql.read_text())
