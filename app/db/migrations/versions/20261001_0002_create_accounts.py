"""create accounts

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-01

intel.accounts (roadmap s11.1 Account, data layer step 7). Enumerated columns are written as
varchar plus one explicit CHECK so this file is a fixed snapshot: later config changes need a
new migration, and enum_check_drift() fails until one exists.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_TENANT_ID = "00000000-0000-4000-8000-000000000001"
LANES = ("salesforce", "servicenow", "sap", "tableau_analytics", "veeva", "data_ai", "other")
DOMAIN_PATTERN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            server_default=sa.text(f"'{DEFAULT_TENANT_ID}'::uuid"),
            nullable=False,
            comment="Single default tenant in v1; Phase 3 adds the tenants table and foreign key",
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "domain",
            sa.Text(),
            nullable=False,
            comment="Normalised: lowercase, no scheme, no www, no path",
        ),
        sa.Column(
            "careers_url",
            sa.Text(),
            nullable=True,
            comment="Only set once verified by visiting; never constructed",
        ),
        sa.Column(
            "careers_url_verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When careers_url was verified by visiting it",
        ),
        sa.Column("industry", sa.Text(), nullable=True),
        sa.Column("primary_lane", sa.String(length=64), nullable=True),
        sa.Column(
            "pursuing_client",
            sa.Text(),
            nullable=True,
            comment="Client the research is for; decides whose history is checked (PRD s3.3)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounts")),
        sa.UniqueConstraint("tenant_id", "domain", name=op.f("uq_accounts_tenant_id_domain")),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_accounts_name_not_blank")),
        sa.CheckConstraint("domain = lower(domain)", name=op.f("ck_accounts_domain_lowercase")),
        sa.CheckConstraint(
            "position('://' in domain) = 0", name=op.f("ck_accounts_domain_no_scheme")
        ),
        sa.CheckConstraint("position('/' in domain) = 0", name=op.f("ck_accounts_domain_no_slash")),
        sa.CheckConstraint("domain NOT LIKE 'www.%%'", name=op.f("ck_accounts_domain_no_www")),
        sa.CheckConstraint(
            f"domain ~ '{DOMAIN_PATTERN}'", name=op.f("ck_accounts_domain_hostname")
        ),
        sa.CheckConstraint(
            "careers_url ~ '^https?://[^/\\s]+'", name=op.f("ck_accounts_careers_url_http")
        ),
        sa.CheckConstraint(
            "(careers_url IS NULL) = (careers_url_verified_at IS NULL)",
            name=op.f("ck_accounts_careers_url_verified_together"),
        ),
        sa.CheckConstraint(
            "pursuing_client IS NULL OR length(btrim(pursuing_client)) > 0",
            name=op.f("ck_accounts_pursuing_client_not_blank"),
        ),
        sa.CheckConstraint(_in_list("primary_lane", LANES), name=op.f("ck_accounts_lane")),
        schema="intel",
        comment="Target companies (roadmap s11.1 Account)",
    )
    op.create_index("ix_accounts_tenant_id_name", "accounts", ["tenant_id", "name"], schema="intel")


def downgrade() -> None:
    op.drop_index(op.f("ix_accounts_tenant_id_name"), table_name="accounts", schema="intel")
    op.drop_table("accounts", schema="intel")
