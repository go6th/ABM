"""Account bundle versions (2026-10-27).

Revision ID: 0088
Revises: 0087
"""

from pathlib import Path

from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create account_bundles."""
    sql = Path(__file__).resolve().parents[1] / "0088_account_bundles.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop it."""
    sql = Path(__file__).resolve().parents[1] / "0088_account_bundles.down.sql"
    op.execute(sql.read_text())
