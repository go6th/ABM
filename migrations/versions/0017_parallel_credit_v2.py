"""Alembic revision 0017: upgrade runs
``0017_parallel_credit_v2.sql``; downgrade runs its ``.down.sql``.
The SQL files hold the schema and its comments. ``down_revision`` is 0016 (export builders)."""

from pathlib import Path

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0017_parallel_credit_v2.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0017_parallel_credit_v2.down.sql"
    op.execute(sql.read_text())
