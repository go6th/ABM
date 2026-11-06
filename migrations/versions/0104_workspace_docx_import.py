"""The Word import columns: source register fields, section kinds, tracker columns
(2026-11-03, workspace import phase 3).

Revision ID: 0104
Revises: 0103
"""

from pathlib import Path

from alembic import op

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the columns and widen the section kinds."""
    sql = Path(__file__).resolve().parents[1] / "0104_workspace_docx_import.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop the columns and restore the section kinds."""
    sql = Path(__file__).resolve().parents[1] / "0104_workspace_docx_import.down.sql"
    op.execute(sql.read_text())
