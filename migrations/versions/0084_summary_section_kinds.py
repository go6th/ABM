"""The Summary's section 1 kinds on report_sections (2026-10-26).

Revision ID: 0084
Revises: 0083
"""

from pathlib import Path

from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the stands_out and lane_meaning kinds and the lane column."""
    sql = Path(__file__).resolve().parents[1] / "0084_summary_section_kinds.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Remove the two kinds and the lane column."""
    sql = Path(__file__).resolve().parents[1] / "0084_summary_section_kinds.down.sql"
    op.execute(sql.read_text())
