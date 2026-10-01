"""Unit tests for the ORM model surface (imports execute the class bodies).

The schema itself is verified against live Postgres by the isolation suite; here we
pin the declared tables, enums and key columns so accidental model drift fails fast.
"""

from __future__ import annotations

from abm_generator.db.models import (
    Account,
    AccountRun,
    AuditLog,
    Base,
    Chunk,
    Fact,
    LedgerEntry,
    Membership,
    RunStatus,
    Tenant,
    ToolCall,
    Visibility,
    Wallet,
    WalletLedger,
)


def test_all_tenant_tables_registered() -> None:
    expected = {
        "tenants",
        "memberships",
        "accounts",
        "account_runs",
        "facts",
        "wallets",
        "wallet_ledger",
        "tool_calls",
        "chunks",
        "audit_log",
    }
    assert expected <= set(Base.metadata.tables), "every tenant table must be declared"


def test_enums_have_spec_values() -> None:
    assert {s.value for s in RunStatus} == {
        "QUEUED",
        "RUNNING",
        "DISTILLING",
        "VERIFYING",
        "AWAITING_APPROVAL",
        "COMPLETED",
        "PARTIAL",
        "FAILED",
        "CANCELED",
        "BUDGET_EXCEEDED",
    }
    assert {s.value for s in Visibility} == {"public", "private"}
    assert {s.value for s in LedgerEntry} == {
        "reserve",
        "commit",
        "refund",
        "topup",
        "adjustment",
    }


def test_tenant_columns() -> None:
    cols = {c.name for c in Tenant.__table__.columns}
    assert {"id", "name", "plan", "isolation_tier", "policy_json", "created_at"} <= cols


def test_membership_pk_is_tenant_and_user() -> None:
    pk = {c.name for c in Membership.__table__.primary_key.columns}
    assert pk == {"tenant_id", "user_id"}


def test_account_unique_domain_per_tenant() -> None:
    names = {i.name for i in Account.__table__.indexes}
    assert "uq_accounts_tenant_domain" in names


def test_fact_columns_include_lineage_and_visibility() -> None:
    cols = {c.name for c in Fact.__table__.columns}
    assert {
        "tenant_id",
        "account_id",
        "attribute_key",
        "value_json",
        "fingerprint",
        "grade",
        "confidence",
        "visibility",
    } <= cols
    names = {i.name for i in Fact.__table__.indexes}
    assert "uq_facts_fingerprint" in names


def test_wallet_and_ledger_shape() -> None:
    assert {"tenant_id", "balance"} <= {c.name for c in Wallet.__table__.columns}
    checks = {c.name for c in Wallet.__table__.constraints}
    assert "ck_wallets_nonneg" in checks, "wallets must not go negative"
    assert {"tenant_id", "run_id", "entry", "amount", "balance_after"} <= {
        c.name for c in WalletLedger.__table__.columns
    }


def test_tool_call_unique_key_is_run_and_args_hash() -> None:
    names = {i.name for i in ToolCall.__table__.indexes}
    assert "uq_tool_calls_run_args" in names, "exactly-once billing key (I4)"


def test_account_run_status_and_idempotency() -> None:
    cols = {c.name for c in AccountRun.__table__.columns}
    assert {"tenant_id", "account_id", "status", "graph_version", "idempotency_key"} <= cols
    indexes = {i.name for i in AccountRun.__table__.indexes}
    assert "uq_account_runs_tenant_idem" in indexes, "idempotent run start"


def test_chunk_links_to_account_and_stays_private() -> None:
    cols = {c.name for c in Chunk.__table__.columns}
    assert {"tenant_id", "account_id", "content", "embedding_json", "visibility"} <= cols
    checks = {c.name for c in Chunk.__table__.constraints}
    assert "ck_chunks_private" in checks, "chunks are never public (ADR-006)"


def test_audit_log_is_append_only_shape() -> None:
    cols = {c.name for c in AuditLog.__table__.columns}
    assert {"tenant_id", "actor", "action", "resource", "before_hash", "after_hash"} <= cols
