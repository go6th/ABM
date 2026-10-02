"""Tracker core domain models."""

from __future__ import annotations

import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Status(StrEnum):
    DONE = "done"
    PARTIAL = "partial"
    NOT_DONE = "not_done"
    BLOCKED = "blocked"
    NONE = "none"


class Priority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    OPTIONAL = "optional"


class Actor(StrEnum):
    STAGE = "stage"
    VERIFIER = "verifier"
    JUDGE = "judge"
    GATE = "gate"
    FINALIZER = "finalizer"


class ReasonCode(StrEnum):
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
    CAP_REACHED = "cap_reached"
    SOURCE_FAILED = "source_failed"
    FEWER_THAN_EXPECTED = "fewer_than_expected"
    OUT_OF_SCOPE = "out_of_scope"


class CallRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    call_ref: str
    tool_category: str
    success: bool
    result_count: int | None = None
    error_kind: str | None = None
    queries: tuple[str, ...] = Field(default_factory=tuple)


class Proof(BaseModel):
    model_config = ConfigDict(frozen=True)
    call_refs: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def proof_hash(self) -> str:
        data = {"call_refs": sorted(self.call_refs)}
        return json.dumps(data, separators=(",", ":"))


class RowEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)
    proof: Proof
    reason_code: ReasonCode | None = None
    detail: str | None = None
