"""Alembic revision 0021: upgrade runs
``0021_identity.sql``; downgrade runs its ``.down.sql``.
The SQL files hold the schema and its comments. ``down_revision`` is 0020 (tenancy)."""

from pathlib import Path

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0021_identity.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0021_identity.down.sql"
    op.execute(sql.read_text())
