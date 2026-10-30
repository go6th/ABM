"""Purge one account the user already deleted: lookup and purge functions, roles and guards.

Revision ID: 0096
Revises: 0095
"""

from pathlib import Path

from alembic import op

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the roles, grants, guards and the two functions."""
    sql = Path(__file__).resolve().parents[1] / "0096_purge_deleted_account.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Remove them again."""
    sql = Path(__file__).resolve().parents[1] / "0096_purge_deleted_account.down.sql"
    op.execute(sql.read_text())
