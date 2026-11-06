"""A model fallback is a usage event: kind 'model_fallback' and a detail object (2026-11-03).

Revision ID: 0100
Revises: 0099
"""

from pathlib import Path

from alembic import op

revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow the kind and add the detail column."""
    sql = Path(__file__).resolve().parents[1] / "0100_model_fallback_usage_event.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Back to the five kinds without a detail column."""
    sql = Path(__file__).resolve().parents[1] / "0100_model_fallback_usage_event.down.sql"
    op.execute(sql.read_text())
