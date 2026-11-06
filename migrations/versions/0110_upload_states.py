"""Document states scan_failed and abandoned, and documents.scan_attempts (2026-11-04, PR4 part B).

Revision ID: 0110
Revises: 0109
"""

from pathlib import Path

from alembic import op

revision = "0110"
down_revision = "0109"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the counter and widen the status and reason checks."""
    sql = Path(__file__).resolve().parents[1] / "0110_upload_states.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Turn the new states into rejected rows and restore the checks."""
    sql = Path(__file__).resolve().parents[1] / "0110_upload_states.down.sql"
    op.execute(sql.read_text())
