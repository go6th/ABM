"""Research files: uploads, parsed units, audit findings, model call log.

Revision ID: 0051
Revises: 0050
"""

from pathlib import Path

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the research file tables (forced RLS) and the orphan discovery function."""
    op.execute(Path("migrations/0051_research_files.sql").read_text())


def downgrade() -> None:
    """Drop the research file tables and the function."""
    op.execute(Path("migrations/0051_research_files.down.sql").read_text())
