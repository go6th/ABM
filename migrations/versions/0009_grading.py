from pathlib import Path
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0009_grading.sql"
    op.execute(sql.read_text())

def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0009_grading.down.sql"
    op.execute(sql.read_text())
