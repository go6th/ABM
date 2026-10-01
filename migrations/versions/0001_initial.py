"""initial schema: tenants, runs, facts, ledgers, chunks, audit + forced RLS

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-01

Creates every Phase 0 table, enables and FORCES row-level security on tenant-owned
tables with fail-closed policies (I1, I2), creates the two-output public_reader policy
(ADR-006), the checkpoint prefix policies (ADR-002) and insert-only grants for
ledger/audit tables. Runs as the migrator (owner) role only.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("plan", sa.String(50), nullable=False, server_default="free"),
        sa.Column("isolation_tier", sa.String(20), nullable=False, server_default="pooled"),
        sa.Column("policy_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "memberships",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("user_id", sa.String(200), primary_key=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.CheckConstraint("role IN ('admin','seller','viewer')", name="ck_memberships_role"),
    )

    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.UniqueConstraint("tenant_id", "domain", name="uq_accounts_tenant_domain"),
    )
    op.create_index("ix_accounts_tenant_id", "accounts", ["tenant_id"])

    op.create_table(
        "account_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="QUEUED"),
        sa.Column("graph_version", sa.String(100), nullable=False),
        sa.Column("budget_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("reserved_credits", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_account_runs_tenant_idem"),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','DISTILLING','VERIFYING','AWAITING_APPROVAL',"
            "'COMPLETED','PARTIAL','FAILED','CANCELED','BUDGET_EXCEEDED')",
            name="ck_account_runs_status",
        ),
    )
    op.create_index("ix_account_runs_tenant_id", "account_runs", ["tenant_id"])

    op.create_table(
        "facts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("attribute_key", sa.String(200), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("grade", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("visibility", sa.String(10), nullable=False, server_default="private"),
        sa.UniqueConstraint(
            "tenant_id",
            "account_id",
            "attribute_key",
            "fingerprint",
            name="uq_facts_fingerprint",
        ),
        sa.CheckConstraint("visibility IN ('public','private')", name="ck_facts_visibility"),
    )
    op.create_index("ix_facts_tenant_id", "facts", ["tenant_id"])

    op.create_table(
        "wallets",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("balance", sa.Numeric(14, 4), nullable=False),
        sa.CheckConstraint("balance >= 0", name="ck_wallets_nonneg"),
    )

    op.create_table(
        "wallet_ledger",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("entry", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(14, 4), nullable=False),
        sa.Column("balance_after", sa.Numeric(14, 4), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "entry IN ('reserve','commit','refund','topup','adjustment')",
            name="ck_wallet_ledger_entry",
        ),
    )
    op.create_index("ix_wallet_ledger_tenant_id", "wallet_ledger", ["tenant_id"])

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool", sa.String(200), nullable=False),
        sa.Column("args_hash", sa.String(64), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("run_id", "args_hash", name="uq_tool_calls_run_args"),
    )
    op.create_index("ix_tool_calls_tenant_id", "tool_calls", ["tenant_id"])

    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("doc_id", sa.Uuid(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding_json", sa.Text(), nullable=True),
        sa.Column("visibility", sa.String(10), nullable=False, server_default="private"),
        sa.CheckConstraint("visibility = 'private'", name="ck_chunks_private"),
    )
    op.create_index("ix_chunks_tenant_id", "chunks", ["tenant_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("action", sa.String(200), nullable=False),
        sa.Column("resource", sa.String(300), nullable=False),
        sa.Column("before_hash", sa.String(64), nullable=True),
        sa.Column("after_hash", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])

    op.create_table(
        "checkpoints_v0",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("tenant_id", "run_id", name="uq_checkpoints_v0_run"),
    )
    op.create_index("ix_checkpoints_v0_tenant_id", "checkpoints_v0", ["tenant_id"])

    _create_rls()


def _create_rls() -> None:
    """Enable + FORCE RLS with fail-closed tenant policies (I1, I2) and grants."""
    tenant_tables = [
        "memberships",
        "accounts",
        "account_runs",
        "facts",
        "wallets",
        "wallet_ledger",
        "tool_calls",
        "chunks",
        "checkpoints_v0",
        "tenants",
    ]
    for table in tenant_tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

        # tenants table: a tenant row is visible only to itself.
        using = (
            "id = (SELECT app_tenant())"
            if table == "tenants"
            else "tenant_id = (SELECT app_tenant())"
        )
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING ({using})
            WITH CHECK ({using})
            """
        )

    # Fail-closed accessor: unset/empty -> NULL -> zero rows (never an error, never all rows).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_tenant() RETURNS uuid
        LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$
        """
    )

    # Two-output rule (ADR-006): public_reader may see only public facts.
    op.execute(
        """
        CREATE POLICY public_only ON facts FOR SELECT TO public_reader
        USING (tenant_id = (SELECT app_tenant()) AND visibility = 'public')
        """
    )
    op.execute("GRANT SELECT ON facts TO public_reader")

    # Checkpoint tables (library-owned, ADR-002): prefix policy on thread_id.
    for table in CHECKPOINT_TABLES:
        op.execute(
            f"""
            DO $do$
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.tables
                           WHERE table_name = '{table}' AND table_schema = 'public') THEN
                    EXECUTE 'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY';
                    EXECUTE 'ALTER TABLE {table} FORCE ROW LEVEL SECURITY';
                    EXECUTE 'CREATE POLICY tenant_prefix ON {table}
                             USING (starts_with(thread_id, (SELECT app_tenant())::text || '':''))
                             WITH CHECK (starts_with(thread_id, (SELECT app_tenant())::text || '':''))';
                    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_rw';
                END IF;
            END
            $do$;
            """
        )

    # Grants: runtime role.
    for table in tenant_tables:
        if table in {"wallet_ledger", "audit_log"}:
            op.execute(f"GRANT SELECT, INSERT ON {table} TO app_rw")  # insert-only (I4, audit)
        else:
            op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_rw")
    op.execute("GRANT USAGE ON SCHEMA public TO app_rw, public_reader")


def downgrade() -> None:
    for table in (
        "checkpoints_v0",
        "audit_log",
        "chunks",
        "tool_calls",
        "wallet_ledger",
        "wallets",
        "facts",
        "account_runs",
        "accounts",
        "memberships",
        "tenants",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS app_tenant()")
    op.execute("DROP EXTENSION IF EXISTS vector")
