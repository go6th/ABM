"""Alembic revision 0012: upgrade runs
``0012_job_pipeline.sql``; downgrade runs its ``.down.sql`` (where one exists).
The SQL files hold the schema and its comments."""

from pathlib import Path

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0012_job_pipeline.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0012_job_pipeline.down.sql"
    op.execute(sql.read_text())
