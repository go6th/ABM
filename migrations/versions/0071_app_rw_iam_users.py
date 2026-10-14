"""The cloud IAM database users join app_rw (APP_RW_IAM_USERS; a no-op when unset).

Revision ID: 0071
Revises: 0070
"""

import os
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def _names() -> None:
    """Hand APP_RW_IAM_USERS to the SQL file as a transaction-local setting (bound, not spliced)."""
    op.get_bind().execute(
        sa.text("SELECT set_config('app.rw_iam_users', :names, true)"),
        {"names": os.environ.get("APP_RW_IAM_USERS", "")},
    )


def upgrade() -> None:
    """Grant app_rw to each named IAM user that exists."""
    _names()
    op.execute(Path("migrations/0071_app_rw_iam_users.sql").read_text())


def downgrade() -> None:
    """Revoke it again."""
    _names()
    op.execute(Path("migrations/0071_app_rw_iam_users.down.sql").read_text())
