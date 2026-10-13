"""Durable runs: stop detail on runs and orphaned-run discovery.

Revision ID: 0050
Revises: 0049
"""

from pathlib import Path

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add account_runs.stop_detail and the runs_orphaned definer function."""
    op.execute(Path("migrations/0050_durable_runs.sql").read_text())


def downgrade() -> None:
    """Remove the function and the column."""
    op.execute(Path("migrations/0050_durable_runs.down.sql").read_text())
