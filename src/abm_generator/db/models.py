"""SQLAlchemy models for Phase 0 tables.

Every tenant-owned table carries ``tenant_id NOT NULL`` with a foreign key to tenants;
RLS policies and grants live in the migrations (expand/contract), not here.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all models."""


class RunStatus(str, enum.Enum):
    """Run lifecycle states (Spec 5.1)."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DISTILLING = "DISTILLING"
    VERIFYING = "VERIFYING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class Visibility(str, enum.Enum):
    """Two-output rule labels (ADR-006)."""

    PUBLIC = "public"
    PRIVATE = "private"


class LedgerEntry(str, enum.Enum):
    """Wallet ledger entry kinds (ADR-007)."""

    RESERVE = "reserve"
    COMMIT = "commit"
    REFUND = "refund"
    TOPUP = "topup"
    ADJUSTMENT = "adjustment"


class Tenant(Base):
    """A tenant (customer organisation)."""

    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    plan: Mapped[str] = mapped_column(String(50), nullable=False, default="free")
    isolation_tier: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pooled"
    )  # pooled | bridge | silo
    policy_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Membership(Base):
    """User membership in a tenant with a role."""

    __tablename__ = "memberships"

    tenant_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # admin|seller|viewer


class Account(Base):
    """A target company researched for a tenant (unique domain per tenant)."""

    __tablename__ = "accounts"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String(253), nullable=False)

    __table_args__ = (
        Index("uq_accounts_tenant_domain", "tenant_id", "domain", unique=True),
        {"sqlite_autoincrement": False},
    )


class AccountRun(Base):
    """One research run for an account; pinned graph version and reserved credits."""

    __tablename__ = "account_runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    account_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("accounts.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="QUEUED")
    graph_version: Mapped[str] = mapped_column(String(100), nullable=False)
    budget_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    reserved_credits: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0")
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("uq_account_runs_tenant_idem", "tenant_id", "idempotency_key", unique=True),
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','DISTILLING','VERIFYING','AWAITING_APPROVAL',"
            "'COMPLETED','PARTIAL','FAILED','CANCELED','BUDGET_EXCEEDED')",
            name="ck_account_runs_status",
        ),
    )


class Fact(Base):
    """A graded fact about an account, with visibility for the two-output rule."""

    __tablename__ = "facts"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    account_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("accounts.id"), nullable=False)
    attribute_key: Mapped[str] = mapped_column(String(200), nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    grade: Mapped[str] = mapped_column(String(20), nullable=False)  # A..D|conflicting|blocked
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    visibility: Mapped[str] = mapped_column(
        String(10), nullable=False, default="private"
    )  # public|private (ADR-006)

    __table_args__ = (
        Index(
            "uq_facts_fingerprint",
            "tenant_id",
            "account_id",
            "attribute_key",
            "fingerprint",
            unique=True,
        ),
        CheckConstraint("visibility IN ('public','private')", name="ck_facts_visibility"),
    )


class Wallet(Base):
    """Current credit balance per tenant; the ledger is the audit trail."""

    __tablename__ = "wallets"

    tenant_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), primary_key=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)

    __table_args__ = (CheckConstraint("balance >= 0", name="ck_wallets_nonneg"),)


class WalletLedger(Base):
    """Insert-only wallet ledger: reserve/commit/refund/topup/adjustment."""

    __tablename__ = "wallet_ledger"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    entry: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ToolCall(Base):
    """Tool-call ledger: exactly-once billing keyed by (run_id, args_hash) (I4)."""

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    tool: Mapped[str] = mapped_column(String(200), nullable=False)
    args_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("uq_tool_calls_run_args", "run_id", "args_hash", unique=True),)


class Chunk(Base):
    """Vector chunk placeholder (Phase B); visibility pinned to private by policy."""

    __tablename__ = "chunks"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    account_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("accounts.id"), nullable=False)
    doc_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(10), nullable=False, default="private")

    __table_args__ = (CheckConstraint("visibility = 'private'", name="ck_chunks_private"),)


class AuditLog(Base):
    """Append-only audit trail (no UPDATE/DELETE grants to the runtime role)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    resource: Mapped[str] = mapped_column(String(300), nullable=False)
    before_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
