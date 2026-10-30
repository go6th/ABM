"""Exclusion hash of the web tables, the newest-fact index, and the full deletion allow-list.

Revision ID: 0093
Revises: 0092
"""

from pathlib import Path

from alembic import op

revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the exclusion hash, the reset function, the index and the wider eraser allow-list."""
    sql = Path(__file__).resolve().parents[1] / "0093_leakage_fix_wave_d2.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Remove them again."""
    sql = Path(__file__).resolve().parents[1] / "0093_leakage_fix_wave_d2.down.sql"
    op.execute(sql.read_text())
