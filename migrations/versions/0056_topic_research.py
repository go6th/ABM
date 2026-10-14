"""Research jobs on a topic the user types (scope 'topic' with the topic text).

Revision ID: 0056
Revises: 0055
"""

from pathlib import Path

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the topic scope and column."""
    op.execute(Path("migrations/0056_topic_research.sql").read_text())


def downgrade() -> None:
    """Remove topic jobs and the column."""
    op.execute(Path("migrations/0056_topic_research.down.sql").read_text())
