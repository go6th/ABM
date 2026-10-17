"""The raw file store: uploaded bytes kept content-addressed (2026-10-17).

Revision ID: 0078
Revises: 0077
"""

from pathlib import Path

from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add raw_files."""
    sql = Path(__file__).resolve().parents[1] / "0078_raw_files.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop raw_files."""
    sql = Path(__file__).resolve().parents[1] / "0078_raw_files.down.sql"
    op.execute(sql.read_text())
