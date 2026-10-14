"""The nightly isolation check: its dedicated pair of check clients.

Revision ID: 0061
Revises: 0060
"""

from pathlib import Path

from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the pair table."""
    op.execute(Path("migrations/0061_isolation_check_pair.sql").read_text())


def downgrade() -> None:
    """Drop it."""
    op.execute(Path("migrations/0061_isolation_check_pair.down.sql").read_text())
