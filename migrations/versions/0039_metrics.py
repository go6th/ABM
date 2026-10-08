"""metrics

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-08 09:00:00.000000
"""
from pathlib import Path

from alembic import op

revision = '0039'
down_revision = '0038'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute(Path("migrations/0039_metrics.sql").read_text())

def downgrade() -> None:
    op.execute(Path("migrations/0039_metrics.down.sql").read_text())
