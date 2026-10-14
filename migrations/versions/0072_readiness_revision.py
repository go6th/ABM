"""The runtime role may read the schema revision (readiness).

Revision ID: 0072
Revises: 0071
"""

from pathlib import Path

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Grant SELECT on alembic_version to app_rw."""
    op.execute(Path("migrations/0072_readiness_revision.sql").read_text())


def downgrade() -> None:
    """Revoke it."""
    op.execute(Path("migrations/0072_readiness_revision.down.sql").read_text())
