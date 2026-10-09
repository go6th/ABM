"""Tenant-owned chat history and atomic spend/turn limits.

Revision ID: 0045
Revises: 0044
"""

from pathlib import Path

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the chat thread ledger and tenant-isolated turns."""
    op.execute(Path("migrations/0045_chat.sql").read_text())


def downgrade() -> None:
    """Remove chat history and its budget ledger."""
    op.execute(Path("migrations/0045_chat.down.sql").read_text())
