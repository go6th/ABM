"""Indexes for document retrieval in Ask (audit 2026-10-15).

Revision ID: 0074
Revises: 0073
"""

from pathlib import Path

from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Account and text indexes and the retrieval vector column on chunks."""
    sql = Path(__file__).resolve().parents[1] / "0074_retrieval_indexes.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop them."""
    sql = Path(__file__).resolve().parents[1] / "0074_retrieval_indexes.down.sql"
    op.execute(sql.read_text())
