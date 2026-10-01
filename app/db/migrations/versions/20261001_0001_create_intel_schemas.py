"""create intel schemas

Revision ID: 0001
Revises:
Create Date: 2026-10-01

Baseline for data layer v1 (docs/data_layer/PLAN.md section 2): the client-readable ``intel``
schema and the operator-only ``intel_internal`` schema. Tables arrive in later revisions.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA intel")
    op.execute("COMMENT ON SCHEMA intel IS 'Data layer v1: rows deliverables are built from'")
    op.execute("CREATE SCHEMA intel_internal")
    op.execute(
        "COMMENT ON SCHEMA intel_internal IS "
        "'Data layer v1: raw object records and spend; never granted to client-facing roles'"
    )


def downgrade() -> None:
    # RESTRICT: refuses to drop a schema that still holds objects, so a downgrade can never
    # silently destroy data that a later revision forgot to remove.
    op.execute("DROP SCHEMA intel_internal RESTRICT")
    op.execute("DROP SCHEMA intel RESTRICT")
