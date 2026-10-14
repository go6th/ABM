"""Research jobs carry the request id that confirmed them (idempotent Confirm).

Revision ID: 0054
Revises: 0053
"""

from pathlib import Path

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the request id and its unique index."""
    op.execute(Path("migrations/0054_research_job_requests.sql").read_text())


def downgrade() -> None:
    """Drop the request id."""
    op.execute(Path("migrations/0054_research_job_requests.down.sql").read_text())
