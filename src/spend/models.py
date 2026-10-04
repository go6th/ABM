"""Frozen spend models. Money fields are integer micro-dollars (``*_micros``); credits are
``Decimal``; nothing here is a float. ``CapProposal.content_hash`` is the SHA-256 of its
canonical JSON, the value an approval binds to (K3).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LineStatus = Literal["priced", "sample", "unknown", "excluded_balance"]
BalanceStatus = Literal["ok", "low", "empty", "unknown"]
Priority = Literal["High", "Medium", "Optional"]
CapEventKind = Literal[
    "proposed",
    "approved",
    "rejected",
    "expired",
    "superseded",
    "extension_requested",
    "extension_declined",
]
PrincipalKind = Literal["human", "tenant_policy", "agent"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PlanItem(_Frozen):
    """One pending paid operation of the plan: which row it serves, how many calls, its priority."""

    row_key: str
    stage: str
    priority: Priority
    provider_code: str
    operation: str
    calls: int = Field(ge=1)
    mandatory: bool = False


class OpCost(_Frozen):
    """Per-call cost bounds of one operation from the provider cost table (micro-dollars).

    ``known`` is False when the table has no figure (cost model ``unknown``): such an operation is
    priced only through an approved sample line (K5)."""

    provider_code: str
    operation: str
    low_micros: int = Field(ge=0)
    expected_micros: int = Field(ge=0)
    high_micros: int = Field(ge=0)
    known: bool
    cost_source: str
    unit_credits: Decimal | None = None

    @model_validator(mode="after")
    def _ordered(self) -> OpCost:
        if not self.low_micros <= self.expected_micros <= self.high_micros:
            raise ValueError("cost bounds must satisfy low <= expected <= high")
        return self


class BalanceSnapshot(_Frozen):
    """A provider balance at a time, never inferred (K15)."""

    provider_code: str
    status: BalanceStatus
    balance: Decimal | None = None
    unit: str | None = None
    source: str
    taken_at: datetime


class BreakdownLine(_Frozen):
    """One advisory line of the breakdown (D-29): per provider, category and operation."""

    provider_code: str
    category: str
    operation: str
    row_keys: tuple[str, ...]
    calls: int
    low_micros: int
    expected_micros: int
    high_micros: int
    status: LineStatus
    note: str | None = None


class BlockedPreview(_Frozen):
    """Rows that would be Blocked (``cap_reached``) at a given cap."""

    cap_micros: int
    rows: tuple[str, ...]


class CapProposal(_Frozen):
    """The proposal a human approves: every figure computed in code from the plan and the cost
    table, every unknown named."""

    run_id: str
    lines: tuple[BreakdownLine, ...]
    expected_micros: int
    high_micros: int
    headroom_pct: int
    cap_micros: int = Field(gt=0)
    default_cap_micros: int
    minimum_viable_micros: int
    blocked_previews: tuple[BlockedPreview, ...]
    sample_operations: tuple[str, ...]
    unknown_operations: tuple[str, ...]
    excluded_providers: tuple[str, ...]
    snapshot_taken_at: tuple[str, ...]
    assumptions: tuple[str, ...]
    config_hash: str

    def canonical_json(self) -> str:
        """Sorted-key JSON with no whitespace (the bytes the hash is taken over)."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @property
    def content_hash(self) -> str:
        """SHA-256 of the canonical JSON."""
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


class Principal(_Frozen):
    """Who approves: a human (with tenant and role from the verified token) or a tenant policy.
    An ``agent`` principal exists only so that a test can prove it is refused."""

    kind: PrincipalKind
    subject: str = Field(min_length=1, max_length=200)
    tenant_id: str
    role: str | None = None


class CapVersion(_Frozen):
    """One immutable cap version of a run."""

    run_id: str
    version: int = Field(ge=1)
    cap_micros: int = Field(gt=0)
    proposal_hash: str
    config_hash: str
    created_at: datetime


class CapEvent(_Frozen):
    """One event on a cap version (append-only)."""

    run_id: str
    version: int
    event: CapEventKind
    principal: str | None = None
    reason: str | None = None
    at: datetime


class SpendLine(_Frozen):
    """Spend of one group (provider, category, operation or route) derived from the ledger."""

    key: str
    paid_calls: int
    replays: int | None  # None: not measured (replays write no ledger event)
    failed_after_charge: int
    open_reserves: int
    credits: Decimal | None
    spent_micros: int
    unknown_cost_calls: int
    possible_duplicates: int
    at_risk_micros: int


class Rollup(_Frozen):
    """Everything the report is built from (the report is a pure function of this)."""

    run_id: str
    account_domain: str
    status: str
    attended: bool
    cap_micros: int | None
    cap_versions: tuple[tuple[int, int, str | None, str | None], ...]  # version, cap, principal, at
    spent_micros: int  # account_runs.spent_usd
    ledger_micros: int  # the ledger sum
    overshoot_micros: int
    by_provider: tuple[SpendLine, ...]
    by_category: tuple[SpendLine, ...]
    by_operation: tuple[SpendLine, ...]
    advisory: dict[str, int]  # provider code -> expected micros of the approved breakdown
    blocked_rows: dict[str, tuple[str, ...]]  # reason -> row keys
    extensions: int
    llm_micros: int | None
    balances_start: tuple[BalanceSnapshot, ...]
    balances_end: tuple[BalanceSnapshot, ...]
    estimated_avoided_micros: int | None


class SpendReport(_Frozen):
    """The internal operator report (never a client file)."""

    run_id: str
    status: Literal["reconciled", "reconciliation_failed"]
    body: dict[str, object]
    markdown: str

    @property
    def content_hash(self) -> str:
        """SHA-256 of the canonical JSON body (deterministic: same rollup, same bytes)."""
        return hashlib.sha256(
            json.dumps(self.body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class TenantSpendView(_Frozen):
    """The allow-listed tenant projection (D-30): categories and call counts only; amounts only
    once D-04 sets a credit price. No provider name or code, no per-call data."""

    run_id: str
    status: str
    categories: dict[str, int]
    total_calls: int
    amounts_shown: bool = False
