"""The entity lock on accounts (2026-10-24).

Revision ID: 0083
Revises: 0082
"""

from pathlib import Path

from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the entity lock columns."""
    sql = Path(__file__).resolve().parents[1] / "0083_entity_lock.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop the entity lock columns."""
    sql = Path(__file__).resolve().parents[1] / "0083_entity_lock.down.sql"
    op.execute(sql.read_text())
