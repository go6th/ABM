"""Alembic revision 0023: upgrade runs
``0023_views.sql``; downgrade runs its ``.down.sql``.
The SQL files hold the schema and its comments. ``down_revision`` is 0022 (audit)."""

from pathlib import Path

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0023_views.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0023_views.down.sql"
    op.execute(sql.read_text())
