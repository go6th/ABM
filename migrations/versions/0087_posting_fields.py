"""Posting fields for the reference workbook and incremental re-research (2026-10-29).

Revision ID: 0087
Revises: 0086
"""

from pathlib import Path

from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add last_seen, seniority and new_since_previous to job_postings; previous_run_id and
    carried to job_pipeline_runs."""
    sql = Path(__file__).resolve().parents[1] / "0087_posting_fields.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop the five columns."""
    sql = Path(__file__).resolve().parents[1] / "0087_posting_fields.down.sql"
    op.execute(sql.read_text())
