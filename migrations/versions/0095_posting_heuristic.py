"""A posting's class may be decided by the heuristic (2026-10-29).

Revision ID: 0095
Revises: 0094
"""

from pathlib import Path

from alembic import op

revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Widen the classified_by check with 'heuristic'."""
    sql = Path(__file__).resolve().parents[1] / "0095_posting_heuristic.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Re-label heuristic rows 'rule' and narrow the check again."""
    sql = Path(__file__).resolve().parents[1] / "0095_posting_heuristic.down.sql"
    op.execute(sql.read_text())
