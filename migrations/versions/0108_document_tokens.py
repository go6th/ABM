"""Tokens on the documents row and embedding lines in the model usage view (2026-11-04, ingestion
hardening PR6).

Revision ID: 0108
Revises: 0107
"""

from pathlib import Path

from alembic import op

revision = "0108"
down_revision = "0107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the four document columns; the usage view gains the embedding batches."""
    sql = Path(__file__).resolve().parents[1] / "0108_document_tokens.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop the columns; the view goes back to 0079."""
    sql = Path(__file__).resolve().parents[1] / "0108_document_tokens.down.sql"
    op.execute(sql.read_text())
