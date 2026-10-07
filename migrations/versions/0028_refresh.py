"""Alembic revision 0028: upgrade runs
``0028_refresh.sql``; downgrade runs its ``.down.sql``.
The SQL files hold the schema and its comments. ``down_revision`` is 0027 (pilot)."""

from pathlib import Path

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0028_refresh.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0028_refresh.down.sql"
    op.execute(sql.read_text())
