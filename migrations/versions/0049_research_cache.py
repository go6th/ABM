"""Model reply log and the cross-run provider call index (research cache).

Revision ID: 0049
Revises: 0048
"""

from pathlib import Path

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the tenant-isolated model reply log and the tool_calls lookup index."""
    op.execute(Path("migrations/0049_research_cache.sql").read_text())


def downgrade() -> None:
    """Remove the model reply log and the lookup index."""
    op.execute(Path("migrations/0049_research_cache.down.sql").read_text())
