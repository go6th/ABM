"""Alembic revision 0010: upgrade runs
``0010_run_integration.sql``; downgrade runs its ``.down.sql`` (where one exists).
The SQL files hold the schema and its comments."""

from pathlib import Path

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0010_run_integration.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0010_run_integration.down.sql"
    op.execute(sql.read_text())
