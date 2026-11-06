"""The members of an uploaded zip as rows, and the audience of a document (2026-11-03,
workspace import phase 1).

Revision ID: 0102
Revises: 0101
"""

from pathlib import Path

from alembic import op

revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add documents.audience and create document_members."""
    sql = Path(__file__).resolve().parents[1] / "0102_document_members.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop document_members and documents.audience."""
    sql = Path(__file__).resolve().parents[1] / "0102_document_members.down.sql"
    op.execute(sql.read_text())
