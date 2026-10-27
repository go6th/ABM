"""The account deletion mark (2026-10-27).

Revision ID: 0087
Revises: 0086
"""

from pathlib import Path

from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add deleted_at and deleted_by on accounts."""
    sql = Path(__file__).resolve().parents[1] / "0087_account_delete.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Remove the mark."""
    sql = Path(__file__).resolve().parents[1] / "0087_account_delete.down.sql"
    op.execute(sql.read_text())
