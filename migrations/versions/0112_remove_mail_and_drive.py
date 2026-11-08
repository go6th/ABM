"""Mail and drive removed: drop what only that feature used (owner order 2026-11-08).

Revision ID: 0112
Revises: 0111
"""

from pathlib import Path

from alembic import op

revision = "0112"
down_revision = "0111"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Delete the mail and drive connections and drop their columns, function and constraints."""
    sql = Path(__file__).resolve().parents[1] / "0112_remove_mail_and_drive.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Bring the columns, function and constraints back, empty."""
    sql = Path(__file__).resolve().parents[1] / "0112_remove_mail_and_drive.down.sql"
    op.execute(sql.read_text())
