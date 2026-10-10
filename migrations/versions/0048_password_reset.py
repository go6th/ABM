"""Add single-use local password reset functions.

Revision ID: 0048
Revises: 0047
"""

from pathlib import Path

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Install narrow token lookup and atomic password replacement functions."""
    op.execute(Path("migrations/0048_password_reset.sql").read_text())


def downgrade() -> None:
    """Remove password reset functions and their narrowly scoped update grant."""
    op.execute(Path("migrations/0048_password_reset.down.sql").read_text())
