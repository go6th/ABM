"""The platform teams table and the import columns on job_postings, people and
tech_detections (2026-11-03, workspace import phase 2).

Revision ID: 0103
Revises: 0102
"""

from pathlib import Path

from alembic import op

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create platform_teams and add the import columns; the erase and purge functions learn
    the new table."""
    sql = Path(__file__).resolve().parents[1] / "0103_platform_teams_and_import_columns.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop the import columns and platform_teams; the functions go back to 0102."""
    sql = Path(__file__).resolve().parents[1] / "0103_platform_teams_and_import_columns.down.sql"
    op.execute(sql.read_text())
