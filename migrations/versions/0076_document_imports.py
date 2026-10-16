"""documents.imported_at: the deliverable-workbook import mark (2026-10-16).

Revision ID: 0076
Revises: 0075
"""

from pathlib import Path

from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the import mark and its pending index."""
    sql = Path(__file__).resolve().parents[1] / "0076_document_imports.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop them."""
    sql = Path(__file__).resolve().parents[1] / "0076_document_imports.down.sql"
    op.execute(sql.read_text())
