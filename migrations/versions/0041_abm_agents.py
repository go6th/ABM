"""abm_agents

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-08 12:00:00.000000
"""

from pathlib import Path

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path("migrations/0041_abm_agents.sql").read_text())


def downgrade() -> None:
    op.execute(Path("migrations/0041_abm_agents.down.sql").read_text())
