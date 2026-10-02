"""Tracker outcome derivation (pure)."""

from __future__ import annotations

from collections.abc import Iterable

from account_intelligence.tracker.config import load_tracker_config
from account_intelligence.tracker.errors import ProofIncomplete
from account_intelligence.tracker.model import CallRecord, ReasonCode, Status


class Outcome:
    def __init__(self, status: Status, reason: ReasonCode | None = None):
        self.status = status
        self.reason = reason

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Outcome):
            return NotImplemented
        return self.status == other.status and self.reason == other.reason

    def __repr__(self) -> str:
        return f"Outcome({self.status.value}, {self.reason.value if self.reason else None})"


def derive_outcome(
    expected_calls: int,
    calls: Iterable[CallRecord],
    cap_hit: bool,
    scope: bool,
    not_attempted_reason: ReasonCode | None,
) -> Outcome:
    config = load_tracker_config()
    out_of_scope_reasons = config.d_13_tracker_rules.get("out_of_scope_reasons", [])

    # 1. Scope out
    if not scope and not_attempted_reason and not_attempted_reason.value in out_of_scope_reasons:
        return Outcome(Status.NONE, ReasonCode.OUT_OF_SCOPE)

    calls_list = list(calls)

    # 2. No calls
    if not calls_list:
        if not not_attempted_reason:
            raise ProofIncomplete("No calls but missing not_attempted_reason")
        return Outcome(Status.NOT_DONE, not_attempted_reason)

    successful = [c for c in calls_list if c.success]
    failed = [c for c in calls_list if not c.success]

    # 3. Successful calls empty, failed calls present -> blocked
    if not successful and failed:
        severity_order = config.d_13_tracker_rules.get("error_severity_order", [])
        worst_error = None
        worst_idx = len(severity_order)
        for f in failed:
            kind = f.error_kind or "unknown"
            try:
                idx = severity_order.index(kind)
            except ValueError:
                idx = len(severity_order)
            if idx < worst_idx:
                worst_idx = idx
                worst_error = kind
        # Fallback to source_failed if kind not found or list is empty
        if not worst_error:
            worst_error = "source_failed"
        return Outcome(
            Status.BLOCKED,
            ReasonCode(worst_error)
            if worst_error in ReasonCode._value2member_map_
            else ReasonCode.SOURCE_FAILED,
        )

    total_results = sum(c.result_count or 0 for c in successful)

    # 4. Results = 0
    if total_results == 0:
        if not failed and len(successful) >= expected_calls:
            return Outcome(Status.NONE)

        reason = ReasonCode.SOURCE_FAILED if failed else ReasonCode.FEWER_THAN_EXPECTED
        return Outcome(Status.PARTIAL, reason)

    # 5. Results >= 1
    if not failed and len(successful) >= expected_calls and not cap_hit:
        return Outcome(Status.DONE)

    reason = (
        ReasonCode.SOURCE_FAILED
        if failed
        else (ReasonCode.CAP_REACHED if cap_hit else ReasonCode.FEWER_THAN_EXPECTED)
    )
    return Outcome(Status.PARTIAL, reason)
