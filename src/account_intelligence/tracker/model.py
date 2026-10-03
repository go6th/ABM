"""Tracker domain models: statuses, actors, reason codes, call records and proof.

Proof is built only from ledger records (T2): a ``ProofCall`` is a copy of what the ledger says
about one call (category, queries, success, result count, error class), never what a stage
claims. ``Proof.proof_hash`` is the SHA-256 of the canonical JSON of the calls (sorted by
reference, no timestamps, no notes), so the same evidence always hashes the same and a replay
after a crash is recognised as ``Unchanged``.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Status(StrEnum):
    """The five row statuses (``assets/checklist.yaml``); unset is ``None``, never a status."""

    DONE = "done"
    PARTIAL = "partial"
    NOT_DONE = "not_done"
    BLOCKED = "blocked"
    NONE = "none"


class Priority(StrEnum):
    """Row priority from the checklist."""

    HIGH = "high"
    MEDIUM = "medium"
    OPTIONAL = "optional"


class Actor(StrEnum):
    """Who is writing (T7)."""

    STAGE = "stage"
    VERIFIER = "verifier"
    JUDGE = "judge"
    GATE = "gate"
    FINALIZER = "finalizer"


class ReasonCode(StrEnum):
    """Configured reason codes (5.4). Each has client text in ``config/tracker_v1.yaml``."""

    WAITING_ON_ACCOUNT_OWNER = "waiting_on_account_owner"
    LANE_NOT_IN_SCOPE = "lane_not_in_scope"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_RATE_LIMITED = "source_rate_limited"
    BUDGET_CAP = "budget_cap"
    TOOL_CALL_CAP = "tool_call_cap"
    PAGE_CAP = "page_cap"
    DEADLINE = "deadline"
    NOT_AVAILABLE_YET = "not_available_yet"
    CLAIMS_DROPPED = "claims_dropped"
    HOSTILE_SOURCE_DROPPED = "hostile_source_dropped"
    SWEPT_FAILED = "swept_failed"
    SWEPT_CANCELED = "swept_canceled"
    SWEPT_BUDGET = "swept_budget"
    SWEPT_DEADLINE = "swept_deadline"
    SWEPT_UNREPORTED = "swept_unreported"
    # Added by this build (listed in the report, not from the sources):
    CAP_REACHED = "cap_reached"
    SOURCE_FAILED = "source_failed"
    FEWER_THAN_EXPECTED = "fewer_than_expected"
    OUT_OF_SCOPE = "out_of_scope"
    RESULT_COUNT_UNKNOWN = "result_count_unknown"
    NOT_PLANNED = "not_planned"


SWEEP_REASONS = frozenset(
    {
        ReasonCode.SWEPT_FAILED,
        ReasonCode.SWEPT_CANCELED,
        ReasonCode.SWEPT_BUDGET,
        ReasonCode.SWEPT_DEADLINE,
        ReasonCode.SWEPT_UNREPORTED,
    }
)


class CallRecord(BaseModel):
    """What the ledger says about one call. ``result_count`` ``None`` means unknown (T3)."""

    model_config = ConfigDict(frozen=True)
    call_ref: str
    tool_category: str
    success: bool
    result_count: int | None = None
    error_kind: str | None = None
    queries: tuple[str, ...] = Field(default_factory=tuple)
    tenant_id: str | None = None
    run_id: str | None = None


class ProofCall(BaseModel):
    """One call as recorded in a row's proof (copied from the ledger, T2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    call_ref: str
    tool_category: str
    success: bool
    result_count: int | None
    error_kind: str | None = None
    queries: tuple[str, ...] = ()

    @classmethod
    def of(cls, record: CallRecord) -> ProofCall:
        """Copy a ledger record into proof form."""
        return cls(
            call_ref=record.call_ref,
            tool_category=record.tool_category,
            success=record.success,
            result_count=record.result_count,
            error_kind=record.error_kind,
            queries=record.queries,
        )


class Proof(BaseModel):
    """A row's proof: the call references, their ledger summaries and downgrade notes."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    call_refs: tuple[str, ...] = Field(default_factory=tuple)
    calls: tuple[ProofCall, ...] = Field(default_factory=tuple)
    notes: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _calls_match_refs(self) -> Proof:
        if self.calls and sorted(c.call_ref for c in self.calls) != sorted(set(self.call_refs)):
            raise ValueError("proof calls must match the proof call references")
        return self

    @classmethod
    def from_calls(cls, calls: list[CallRecord], notes: tuple[str, ...] = ()) -> Proof:
        """Proof built from ledger records, sorted by reference."""
        ordered = sorted({c.call_ref: c for c in calls}.values(), key=lambda c: c.call_ref)
        return cls(
            call_refs=tuple(c.call_ref for c in ordered),
            calls=tuple(ProofCall.of(c) for c in ordered),
            notes=notes,
        )

    @property
    def proof_hash(self) -> str:
        """SHA-256 of the canonical evidence (references and call summaries; no notes)."""
        body = {
            "call_refs": sorted(set(self.call_refs)),
            "calls": [c.model_dump(mode="json") for c in sorted(self.calls, key=_ref)],
        }
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def with_note(self, note: str) -> Proof:
        """Same evidence plus a downgrade note (5.2: downgrades keep the old proof)."""
        return self.model_copy(update={"notes": (*self.notes, note)})


def _ref(call: ProofCall) -> str:
    return call.call_ref


class RowEvidence(BaseModel):
    """Evidence a stage hands to ``record_outcome``: references only, never a status."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    proof: Proof
    reason_code: ReasonCode | None = None
    detail: str | None = None
