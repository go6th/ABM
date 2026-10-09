"""Store tenant BYO provider-key ciphertext protected by Cloud KMS.

Revision ID: 0046
Revises: 0045
"""

from pathlib import Path

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create a forced-RLS table that stores only encrypted BYO-key bytes."""
    op.execute(Path("migrations/0046_tenant_model_credentials.sql").read_text())


def downgrade() -> None:
    """Remove tenant BYO-key ciphertext."""
    op.execute(Path("migrations/0046_tenant_model_credentials.down.sql").read_text())
