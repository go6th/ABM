"""The product as designed: client limits, credit pool, research jobs, schedules, notifications,
access state, question categories, the 6th Library, isolation checks and per-user mail and drive.

Revision ID: 0053
Revises: 0052
"""

from pathlib import Path

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the replica tables (forced RLS on tenant tables) and the added columns."""
    op.execute(Path("migrations/0053_replica.sql").read_text())


def downgrade() -> None:
    """Drop the replica tables and columns and restore the earlier constraints."""
    op.execute(Path("migrations/0053_replica.down.sql").read_text())
