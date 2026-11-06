"""The import status tables: import_runs and import_run_parts (2026-11-04, ingestion hardening PR2).

Revision ID: 0106
Revises: 0105
"""

from pathlib import Path

from alembic import op

revision = "0106"
down_revision = "0105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the two tables; the erase and purge functions learn them."""
    sql = Path(__file__).resolve().parents[1] / "0106_import_runs.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop the two tables; the functions go back to 0105."""
    sql = Path(__file__).resolve().parents[1] / "0106_import_runs.down.sql"
    op.execute(sql.read_text())
