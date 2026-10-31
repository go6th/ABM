"""Index the foreign keys that point at rows the account purge deletes (2026-10-31).

Revision ID: 0097
Revises: 0096
"""

from pathlib import Path

from alembic import op

revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the eighteen indexes."""
    sql = Path(__file__).resolve().parents[1] / "0097_purge_fk_indexes.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop them again."""
    sql = Path(__file__).resolve().parents[1] / "0097_purge_fk_indexes.down.sql"
    op.execute(sql.read_text())
