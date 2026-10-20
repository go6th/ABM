"""The effort a user picks beside each model (owner decision 2026-10-20).

Revision ID: 0080
Revises: 0079
"""

from pathlib import Path

from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the research and analysis effort columns."""
    sql = Path(__file__).resolve().parents[1] / "0080_model_effort.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop the effort columns."""
    sql = Path(__file__).resolve().parents[1] / "0080_model_effort.down.sql"
    op.execute(sql.read_text())
