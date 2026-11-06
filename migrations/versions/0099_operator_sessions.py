"""Opaque staff sessions: only the SHA-256 of the console's session token (2026-11-03).

Revision ID: 0099
Revises: 0098
"""

from pathlib import Path

from alembic import op

revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create operator_sessions."""
    sql = Path(__file__).resolve().parents[1] / "0099_operator_sessions.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop it again."""
    sql = Path(__file__).resolve().parents[1] / "0099_operator_sessions.down.sql"
    op.execute(sql.read_text())
