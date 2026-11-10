"""Remove the owner-only audience: rows become client, checks drop 'owner_only' (2026-11-08).

Revision ID: 0115
Revises: 0114
"""

from pathlib import Path

from alembic import op

revision = "0115"
down_revision = "0114"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Owner-only documents, members and notes become client ones; the checks drop the value."""
    sql = Path(__file__).resolve().parents[1] / "0115_remove_owner_only.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """The audience checks accept 'owner_only' again (the rows stay client)."""
    sql = Path(__file__).resolve().parents[1] / "0115_remove_owner_only.down.sql"
    op.execute(sql.read_text())
