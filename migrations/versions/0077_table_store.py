"""The table store: ingested tables queried whole (tabular questions, 2026-10-17).

Revision ID: 0077
Revises: 0076
"""

from pathlib import Path

from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add document_passages.metadata, document_tables and document_table_rows."""
    sql = Path(__file__).resolve().parents[1] / "0077_table_store.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop them."""
    sql = Path(__file__).resolve().parents[1] / "0077_table_store.down.sql"
    op.execute(sql.read_text())
