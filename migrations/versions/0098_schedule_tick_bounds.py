"""Bound the scheduled-refresh tick: starts per tick and per client (2026-11-03).

Revision ID: 0098
Revises: 0097
"""

from pathlib import Path

from alembic import op

revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the two platform defaults."""
    sql = Path(__file__).resolve().parents[1] / "0098_schedule_tick_bounds.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop them again."""
    sql = Path(__file__).resolve().parents[1] / "0098_schedule_tick_bounds.down.sql"
    op.execute(sql.read_text())
