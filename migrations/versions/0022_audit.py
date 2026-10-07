"""Alembic revision 0022: upgrade runs
``0022_audit.sql``; downgrade runs its ``.down.sql``.
The SQL files hold the schema and its comments. ``down_revision`` is 0021 (identity)."""

from pathlib import Path

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0022_audit.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0022_audit.down.sql"
    op.execute(sql.read_text())
