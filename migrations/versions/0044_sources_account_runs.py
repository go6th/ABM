"""Allow stable source IDs to reference account_runs.

Revision ID: 0044
Revises: 0043
"""

from pathlib import Path

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path("migrations/0044_sources_account_runs.sql").read_text())


def downgrade() -> None:
    op.execute(Path("migrations/0044_sources_account_runs.down.sql").read_text())
