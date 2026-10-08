"""registers

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-08 09:04:00.000000
"""
from pathlib import Path

from alembic import op

revision = '0040'
down_revision = '0039'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute(Path("migrations/0040_registers.sql").read_text())

def downgrade() -> None:
    op.execute(Path("migrations/0040_registers.down.sql").read_text())
