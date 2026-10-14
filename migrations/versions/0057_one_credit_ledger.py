"""One credit truth: every run belongs to a research job; wallets become read-only history.

Revision ID: 0057
Revises: 0056
"""

from pathlib import Path

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Route all credit movement through the pool ledger."""
    op.execute(Path("migrations/0057_one_credit_ledger.sql").read_text())


def downgrade() -> None:
    """Restore the wallet grants and the earlier job kinds."""
    op.execute(Path("migrations/0057_one_credit_ledger.down.sql").read_text())
