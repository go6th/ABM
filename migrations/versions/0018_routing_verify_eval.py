"""Alembic revision 0018: upgrade runs
``0018_routing_verify_eval.sql``; downgrade runs its ``.down.sql``.
The SQL files hold the schema and its comments. ``down_revision`` is 0017 (parallel execution
and credit manager v2)."""

from pathlib import Path

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0018_routing_verify_eval.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0018_routing_verify_eval.down.sql"
    op.execute(sql.read_text())
