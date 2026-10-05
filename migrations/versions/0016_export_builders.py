"""Alembic revision 0016: upgrade runs
``0016_export_builders.sql``; downgrade runs its ``.down.sql``.
The SQL files hold the schema and its comments. ``down_revision`` is 0015 (the spend migration):
re-pointed from 0014 when the credit-cap and export-builder branches met on ``feat/api-v1``."""

from pathlib import Path

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0016_export_builders.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0016_export_builders.down.sql"
    op.execute(sql.read_text())
