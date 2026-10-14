"""Client invites: accept a copied invite link and choose a password.

Revision ID: 0060
Revises: 0059
"""

from pathlib import Path

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the invite acceptance functions."""
    op.execute(Path("migrations/0060_client_invites.sql").read_text())


def downgrade() -> None:
    """Drop them."""
    op.execute(Path("migrations/0060_client_invites.down.sql").read_text())
