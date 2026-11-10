"""SD-10: SSO discovery and JIT trust only DNS-proven domains (2026-11-10).

Revision ID: 0117
Revises: 0116
"""

from pathlib import Path

from alembic import op

revision = "0117"
down_revision = "0116"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """idp_connections.domains_proven_at; discovery and the claim check use proven domains."""
    sql = Path(__file__).resolve().parents[1] / "0117_sso_domain_proof.sql"
    op.execute(sql.read_text())


def downgrade() -> None:
    """Discovery trusts every listed domain again; the column goes."""
    sql = Path(__file__).resolve().parents[1] / "0117_sso_domain_proof.down.sql"
    op.execute(sql.read_text())
