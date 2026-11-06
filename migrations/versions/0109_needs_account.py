"""documents.status 'needs_account': the held-upload state (2026-11-04, ingestion hardening PR5).

Revision ID: 0109
Revises: 0108
"""

from pathlib import Path

from alembic import op

revision = "0109"
down_revision = "0108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the status to the documents check constraint."""
    sql = Path(__file__).resolve().parents[1] / "0109_needs_account.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Held uploads return to parsed; the constraint goes back to 0052's list."""
    sql = Path(__file__).resolve().parents[1] / "0109_needs_account.down.sql"
    op.execute(sql.read_text())
