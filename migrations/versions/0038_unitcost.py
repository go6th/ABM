"""unitcost

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-08 09:00:00.000000
"""

from pathlib import Path

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path("migrations/0038_unitcost.sql").read_text())


def downgrade() -> None:
    op.execute(Path("migrations/0038_unitcost.down.sql").read_text())
