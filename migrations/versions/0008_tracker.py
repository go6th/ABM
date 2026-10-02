from pathlib import Path
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0008_tracker.sql"
    op.execute(sql.read_text())

def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "0008_tracker.down.sql"
    op.execute(sql.read_text())
