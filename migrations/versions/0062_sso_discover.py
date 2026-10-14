"""Sign-in discovery: the company's SSO connection by work-email domain.

Revision ID: 0062
Revises: 0061
"""

from pathlib import Path

from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the lookup function."""
    op.execute(Path("migrations/0062_sso_discover.sql").read_text())


def downgrade() -> None:
    """Drop it."""
    op.execute(Path("migrations/0062_sso_discover.down.sql").read_text())
