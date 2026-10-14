"""Limits per user: researches and chats, their usage, and requests for more.

Revision ID: 0065
Revises: 0064
"""

from pathlib import Path

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the limits, usage and requests."""
    op.execute(Path("migrations/0065_user_limits.sql").read_text())


def downgrade() -> None:
    """Remove them."""
    op.execute(Path("migrations/0065_user_limits.down.sql").read_text())
