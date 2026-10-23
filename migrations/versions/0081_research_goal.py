"""The optional research goal on New research (owner decision 2026-10-23).

Revision ID: 0081
Revises: 0080
"""

from pathlib import Path

from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the research goal column."""
    sql = Path(__file__).resolve().parents[1] / "0081_research_goal.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop the research goal column."""
    sql = Path(__file__).resolve().parents[1] / "0081_research_goal.down.sql"
    op.execute(sql.read_text())
