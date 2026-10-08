"""Alembic revision 0033: upgrade runs
``0033_datamodel.sql``; downgrade runs its ``.down.sql``.
The SQL files hold the schema and its comments. ``down_revision`` is 0032 (crm)."""

from pathlib import Path

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0033_datamodel.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0033_datamodel.down.sql"
    op.execute(sql.read_text())
