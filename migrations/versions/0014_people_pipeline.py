"""Alembic revision 0014: upgrade runs
``0014_people_pipeline.sql``; downgrade runs its ``.down.sql``.
The SQL files hold the schema and its comments. ``down_revision`` is 0013 (the job gap passes), re-pointed when
the people and jobs branches met."""

from pathlib import Path

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0014_people_pipeline.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0014_people_pipeline.down.sql"
    op.execute(sql.read_text())
