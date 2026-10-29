"""The organisation tech stack of a run, stored for the Confirmed stack sheet (2026-10-29).

Revision ID: 0090
Revises: 0089
"""

from pathlib import Path

from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the tech stack table."""
    sql = Path(__file__).resolve().parents[1] / "0090_tech_stack_items.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop the tech stack table."""
    sql = Path(__file__).resolve().parents[1] / "0090_tech_stack_items.down.sql"
    op.execute(sql.read_text())
