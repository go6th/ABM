"""Alembic revision 0015: upgrade runs
``0015_credit_cap.sql``; downgrade runs its ``.down.sql``.
The SQL files hold the schema and its comments."""

from pathlib import Path

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0015_credit_cap.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0015_credit_cap.down.sql"
    op.execute(sql.read_text())
