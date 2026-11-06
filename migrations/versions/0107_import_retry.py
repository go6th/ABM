"""Bounded retry of an import, per part: import_run_parts.next_attempt_at (2026-11-04, ingestion
hardening PR3).

Revision ID: 0107
Revises: 0106
"""

from pathlib import Path

from alembic import op

revision = "0107"
down_revision = "0106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the retry time and its index."""
    sql = Path(__file__).resolve().parents[1] / "0107_import_retry.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop them."""
    sql = Path(__file__).resolve().parents[1] / "0107_import_retry.down.sql"
    op.execute(sql.read_text())
