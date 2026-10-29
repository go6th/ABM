"""Tenant deletion also erases the link archive and the organisation tech stack (2026-10-29).

Revision ID: 0091
Revises: 0090
"""

from pathlib import Path

from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Grant the eraser DELETE on the two tables and extend the erase_rows allow-list."""
    sql = Path(__file__).resolve().parents[1] / "0091_erase_new_tables.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Restore the allow-list of migration 0020."""
    sql = Path(__file__).resolve().parents[1] / "0091_erase_new_tables.down.sql"
    op.execute(sql.read_text())
