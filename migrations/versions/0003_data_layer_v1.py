from pathlib import Path

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    migration_sql = Path(__file__).resolve().parents[1] / "0003_data_layer_v1.sql"
    op.execute(migration_sql.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise RuntimeError(
        "0003_data_layer_v1 is expand-only and contains append-only records; "
        "write a reviewed forward migration instead of downgrading."
    )
