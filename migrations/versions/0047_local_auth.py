"""Add isolated storage and narrow lookups for first-party tenant credentials.

Revision ID: 0047
Revises: 0046
"""

from pathlib import Path

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Install the credential boundary; runtime code cannot query credential tables directly."""
    op.execute(Path("migrations/0047_local_auth.sql").read_text())


def downgrade() -> None:
    """Remove first-party credential storage and its reviewed lookup functions."""
    op.execute(Path("migrations/0047_local_auth.down.sql").read_text())
