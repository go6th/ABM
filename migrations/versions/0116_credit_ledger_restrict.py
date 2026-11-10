"""SD-01: credit_entries no longer cascades from research_jobs; tenant deletion scrubs jobs.

Revision ID: 0116
Revises: 0115
"""

from pathlib import Path

from alembic import op

revision = "0116"
down_revision = "0115"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """fk_credit_entries_job becomes RESTRICT; research_jobs is scrubbed, never erased."""
    sql = Path(__file__).resolve().parents[1] / "0116_credit_ledger_restrict.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """The cascade, the erase allow-list entry and the eraser's DELETE come back."""
    sql = Path(__file__).resolve().parents[1] / "0116_credit_ledger_restrict.down.sql"
    op.execute(sql.read_text())
