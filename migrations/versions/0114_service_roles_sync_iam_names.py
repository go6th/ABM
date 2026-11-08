"""service_roles_sync() accepts Cloud SQL IAM user names (2026-11-08).

Revision ID: 0114
Revises: 0113
"""

from pathlib import Path

from alembic import op

revision = "0114"
down_revision = "0113"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Redefine service_roles_sync() with the by-name member check."""
    sql = Path(__file__).resolve().parents[1] / "0114_service_roles_sync_iam_names.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """service_roles_sync() as 0101 defined it."""
    sql = Path(__file__).resolve().parents[1] / "0114_service_roles_sync_iam_names.down.sql"
    op.execute(sql.read_text())
