"""A reused model reply keeps its text, so a resume of a resume reuses it (audit 2026-10-15).

Revision ID: 0073
Revises: 0072
"""

from pathlib import Path

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Widen ck_model_reply_log_text to cache rows."""
    sql = Path(__file__).resolve().parents[1] / "0073_reply_text_on_reuse.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Narrow it again (clearing the text of cache rows first)."""
    sql = Path(__file__).resolve().parents[1] / "0073_reply_text_on_reuse.down.sql"
    op.execute(sql.read_text())
