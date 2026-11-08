"""Delete in the app erases the account: the purge for the user's own deleted account (2026-11-08).

Revision ID: 0113
Revises: 0112
"""

from pathlib import Path

from alembic import op

revision = "0113"
down_revision = "0112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add purge_own_deleted_account."""
    sql = Path(__file__).resolve().parents[1] / "0113_delete_erases_account.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop purge_own_deleted_account."""
    sql = Path(__file__).resolve().parents[1] / "0113_delete_erases_account.down.sql"
    op.execute(sql.read_text())
