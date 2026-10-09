"""security

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-08 08:59:12.743390
"""

from pathlib import Path

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path("migrations/0037_security.sql").read_text())


def downgrade() -> None:
    op.execute(Path("migrations/0037_security.down.sql").read_text())
