"""A research continues itself: the research_resumed notification kind (2026-10-16).

Revision ID: 0075
Revises: 0074
"""

from pathlib import Path

from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow the research_resumed notification kind."""
    sql = Path(__file__).resolve().parents[1] / "0075_auto_resume.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Remove those notifications and restore the earlier kinds."""
    sql = Path(__file__).resolve().parents[1] / "0075_auto_resume.down.sql"
    op.execute(sql.read_text())
