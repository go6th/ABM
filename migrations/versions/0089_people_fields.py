"""Dated employments on stored profiles for the People List (2026-10-29).

Revision ID: 0089
Revises: 0088
"""

from pathlib import Path

from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add person_profiles.employments and extend the erasure check and trigger over it."""
    sql = Path(__file__).resolve().parents[1] / "0089_people_fields.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Drop person_profiles.employments and restore the 0014 check and trigger."""
    sql = Path(__file__).resolve().parents[1] / "0089_people_fields.down.sql"
    op.execute(sql.read_text())
