"""A posting's class may be decided by the heuristic (2026-10-29).

Revision ID: 0088
Revises: 0087
"""

from pathlib import Path

from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Widen the classified_by check with 'heuristic'."""
    sql = Path(__file__).resolve().parents[1] / "0088_posting_heuristic.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Re-label heuristic rows 'rule' and narrow the check again."""
    sql = Path(__file__).resolve().parents[1] / "0088_posting_heuristic.down.sql"
    op.execute(sql.read_text())
