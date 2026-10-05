"""Alembic revision 0007: upgrade runs
``0007_connector_ledger.sql``; downgrade runs its ``.down.sql`` (where one exists).
The SQL files hold the schema and its comments."""

from pathlib import Path

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0007_connector_ledger.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0007_connector_ledger.down.sql"
    op.execute(sql.read_text())
