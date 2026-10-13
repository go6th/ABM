"""Two research input modes: documents, passages, redaction map, gate and leak check records.

Revision ID: 0052
Revises: 0051
"""

from pathlib import Path

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the document tables (forced RLS), the gate records and the added columns."""
    op.execute(Path("migrations/0052_research_input_modes.sql").read_text())


def downgrade() -> None:
    """Drop the document tables, the gate records and the added columns."""
    op.execute(Path("migrations/0052_research_input_modes.down.sql").read_text())
