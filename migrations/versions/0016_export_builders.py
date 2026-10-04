"""Alembic revision 0016: upgrade runs
``0016_export_builders.sql``; downgrade runs its ``.down.sql``.
The SQL files hold the schema and its comments. ``down_revision`` is 0014 on this branch (0015 is
the spend migration on the unmerged credit-cap branch); re-point it when the branches meet."""

from pathlib import Path

from alembic import op

revision = "0016"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0016_export_builders.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0016_export_builders.down.sql"
    op.execute(sql.read_text())
