"""Alembic revision 0005: upgrade runs
``0005_sources.sql``; downgrade runs its ``.down.sql`` (where one exists).
The SQL files hold the schema and its comments."""

from pathlib import Path

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0005_sources.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0005_sources.down.sql"
    op.execute(sql.read_text())
