"""``derive_outcome``: a row's status from ledger evidence (section 5.3, proposal D-13). Pure.

Rules in order:

1. scope out with an allowed, verified reason: none (``out_of_scope``);
2. no calls: not_done with the not-attempted reason (missing reason: ``ProofIncomplete``);
3. only failed calls: blocked, reason from the most severe error class;
4. no known results: none when every call succeeded, reported a count, and at least
   ``expected_calls`` did; otherwise partial;
5. results: done when nothing failed, enough calls reported a count and no cap was hit;
   otherwise partial with the reason.

T3: a successful call whose result count is unknown never supports done or none.
"""

from __future__ import annotations

from collections.abc import Iterable

from account_intelligence.tracker.config import TrackerConfig, load_tracker_config
from account_intelligence.tracker.errors import ProofIncomplete
from account_intelligence.tracker.model import CallRecord, ReasonCode, Status


class Outcome:
    """A derived status and its reason code."""

    def __init__(self, status: Status, reason: ReasonCode | None = None):
        self.status = status
        self.reason = reason

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Outcome):
            return NotImplemented
        return self.status == other.status and self.reason == other.reason

    def __hash__(self) -> int:
        return hash((self.status, self.reason))

    def __repr__(self) -> str:
        return f"Outcome({self.status.value}, {self.reason.value if self.reason else None})"


def blocked_reason(failed: list[CallRecord], config: TrackerConfig) -> ReasonCode:
    """Reason for a blocked row: the most severe failed call's class, mapped to a code."""
    order = config.error_severity_order

    def rank(call: CallRecord) -> tuple[int, str]:
        kind = call.error_kind or ""
        return (order.index(kind) if kind in order else len(order), kind)

    worst = min(failed, key=rank).error_kind or ""
    return ReasonCode(config.error_reason_map.get(worst, ReasonCode.SOURCE_FAILED.value))


def derive_outcome(
    expected_calls: int,
    calls: Iterable[CallRecord],
    cap_hit: bool,
    scope: bool,
    not_attempted_reason: ReasonCode | None,
    config: TrackerConfig | None = None,
) -> Outcome:
    """Derive the status. Deterministic and independent of call order."""
    cfg = config or load_tracker_config()
    if (
        not scope
        and not_attempted_reason is not None
        and not_attempted_reason.value in cfg.out_of_scope_reasons
    ):
        return Outcome(Status.NONE, ReasonCode.OUT_OF_SCOPE)

    calls_list = list(calls)
    if not calls_list:
        if not_attempted_reason is None:
            raise ProofIncomplete("no calls and no not-attempted reason")
        return Outcome(Status.NOT_DONE, not_attempted_reason)

    successful = [c for c in calls_list if c.success]
    failed = [c for c in calls_list if not c.success]
    if not successful:
        return Outcome(Status.BLOCKED, blocked_reason(failed, cfg))

    counted = [c for c in successful if c.result_count is not None]
    results = sum(c.result_count or 0 for c in counted)
    unknown = len(counted) < len(successful)
    enough = len(counted) >= max(expected_calls, 1)

    if results == 0:
        if not failed and not unknown and enough:
            return Outcome(Status.NONE)
        if failed:
            return Outcome(Status.PARTIAL, ReasonCode.SOURCE_FAILED)
        if unknown:
            return Outcome(Status.PARTIAL, ReasonCode.RESULT_COUNT_UNKNOWN)
        return Outcome(Status.PARTIAL, ReasonCode.FEWER_THAN_EXPECTED)

    if not failed and enough and not cap_hit:
        return Outcome(Status.DONE)
    if failed:
        return Outcome(Status.PARTIAL, ReasonCode.SOURCE_FAILED)
    if cap_hit:
        return Outcome(Status.PARTIAL, ReasonCode.CAP_REACHED)
    if unknown:
        return Outcome(Status.PARTIAL, ReasonCode.RESULT_COUNT_UNKNOWN)
    return Outcome(Status.PARTIAL, ReasonCode.FEWER_THAN_EXPECTED)
