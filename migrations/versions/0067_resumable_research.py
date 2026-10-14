"""Resumable research: the dollar cap, the repeat detector, stalls and resume.

Revision ID: 0067
Revises: 0066
"""

from pathlib import Path

from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the cap, the detector, the heartbeat and the resume link."""
    op.execute(Path("migrations/0067_resumable_research.sql").read_text())


def downgrade() -> None:
    """Remove them."""
    op.execute(Path("migrations/0067_resumable_research.down.sql").read_text())
