"""Download everything: the archive jobs.

Revision ID: 0068
Revises: 0067
"""

from pathlib import Path

from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the archive jobs."""
    op.execute(Path("migrations/0068_download_everything.sql").read_text())


def downgrade() -> None:
    """Remove them."""
    op.execute(Path("migrations/0068_download_everything.down.sql").read_text())
