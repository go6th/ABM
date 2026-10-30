"""The link archive reaches erasure, keeps its index fields and lets the newest status win.

Revision ID: 0092
Revises: 0091
"""

from pathlib import Path

from alembic import op

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add meta, the forward-only archived_at update and the reviewed erase function."""
    sql = Path(__file__).resolve().parents[1] / "0092_archive_erasure_meta.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Remove them again."""
    sql = Path(__file__).resolve().parents[1] / "0092_archive_erasure_meta.down.sql"
    op.execute(sql.read_text())
