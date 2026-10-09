"""abm_distill

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-08 12:00:00.000000
"""

from pathlib import Path

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path("migrations/0042_abm_distill.sql").read_text())


def downgrade() -> None:
    op.execute(Path("migrations/0042_abm_distill.down.sql").read_text())
