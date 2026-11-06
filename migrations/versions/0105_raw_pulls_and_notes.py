"""The raw pull and note tables: news events, person comments, organisation connections,
research notes, pull log calls (2026-11-03, workspace import phase 4).

Revision ID: 0105
Revises: 0104
"""

from pathlib import Path

from alembic import op

revision = "0105"
down_revision = "0104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the five tables; the erase and purge functions learn them."""
    sql = Path(__file__).resolve().parents[1] / "0105_raw_pulls_and_notes.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop the five tables; the functions go back to 0103."""
    sql = Path(__file__).resolve().parents[1] / "0105_raw_pulls_and_notes.down.sql"
    op.execute(sql.read_text())
