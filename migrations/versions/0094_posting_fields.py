"""Posting fields for the reference workbook and incremental re-research (2026-10-29).

Revision ID: 0094
Revises: 0093
"""

from pathlib import Path

from alembic import op

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add last_seen, seniority and new_since_previous to job_postings; previous_run_id and
    carried to job_pipeline_runs."""
    sql = Path(__file__).resolve().parents[1] / "0094_posting_fields.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop the five columns."""
    sql = Path(__file__).resolve().parents[1] / "0094_posting_fields.down.sql"
    op.execute(sql.read_text())
