"""The link archive: every stored address kept with its captured body (2026-10-27).

Revision ID: 0086
Revises: 0085
"""

from pathlib import Path

from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the link archive."""
    sql = Path(__file__).resolve().parents[1] / "0086_link_archive.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop the link archive."""
    sql = Path(__file__).resolve().parents[1] / "0086_link_archive.down.sql"
    op.execute(sql.read_text())
