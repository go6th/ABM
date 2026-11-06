"""The runtime role split by service: app_api, app_worker, app_sweeper (2026-11-03, ADR-0123).

Revision ID: 0101
Revises: 0100
"""

from pathlib import Path

from alembic import op

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the roles and the sync function, then sync once (``migrations/env.py`` hands the
    IAM user settings to every run and syncs again after it)."""
    sql = Path(__file__).resolve().parents[1] / "0101_service_roles.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop app_api's grants and policy copies and the sync function."""
    sql = Path(__file__).resolve().parents[1] / "0101_service_roles.down.sql"
    op.execute(sql.read_text())
