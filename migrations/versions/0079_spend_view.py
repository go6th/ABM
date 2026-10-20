"""Model spend per day by surface, task kind and model (token review, 2026-10-20).

Revision ID: 0079
Revises: 0078
"""

from pathlib import Path

from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the batch flag, the Ask turn's surface and paid model, and llm_spend_daily."""
    sql = Path(__file__).resolve().parents[1] / "0079_spend_view.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop the view and the columns."""
    sql = Path(__file__).resolve().parents[1] / "0079_spend_view.down.sql"
    op.execute(sql.read_text())
