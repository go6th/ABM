"""Model choice: enabled and allowed models, user preferences, what runs used.

Revision ID: 0064
Revises: 0063
"""

from pathlib import Path

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the model choice columns and preferences."""
    op.execute(Path("migrations/0064_model_choice.sql").read_text())


def downgrade() -> None:
    """Remove them."""
    op.execute(Path("migrations/0064_model_choice.down.sql").read_text())
