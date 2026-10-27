"""The no-progress slice counter on account_runs (2026-10-27).

Revision ID: 0085
Revises: 0084
"""

from pathlib import Path

from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the counter of consecutive slices that moved nothing."""
    sql = Path(__file__).resolve().parents[1] / "0085_no_progress_slices.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Remove the counter."""
    sql = Path(__file__).resolve().parents[1] / "0085_no_progress_slices.down.sql"
    op.execute(sql.read_text())
