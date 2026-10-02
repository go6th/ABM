from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    sql = Path(__file__).resolve().parents[1] / "0001_core.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS wallet_ledger, wallets, tool_calls, facts, accounts, "
        "memberships, tenants CASCADE"
    )
