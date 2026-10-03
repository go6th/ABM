"""Optimistic run-state transition rules shared by persistence adapters."""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    """The run states of skill reference 04."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    DISTILLING = "DISTILLING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


TERMINAL = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.PARTIAL,
        RunStatus.BUDGET_EXCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELED,
    }
)
TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELED, RunStatus.FAILED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.AWAITING_APPROVAL,
            RunStatus.DISTILLING,
            RunStatus.FAILED,
            RunStatus.CANCELED,
        }
    ),
    RunStatus.AWAITING_APPROVAL: frozenset(
        {RunStatus.RUNNING, RunStatus.CANCELED, RunStatus.FAILED}
    ),
    RunStatus.DISTILLING: frozenset(
        {
            RunStatus.VERIFYING,
            RunStatus.FAILED,
            RunStatus.CANCELED,
        }
    ),
    RunStatus.VERIFYING: frozenset(
        {
            RunStatus.COMPLETED,
            RunStatus.PARTIAL,
            RunStatus.BUDGET_EXCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELED,
        }
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.PARTIAL: frozenset(),
    RunStatus.BUDGET_EXCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELED: frozenset(),
}


def transition(expected: RunStatus, new: RunStatus, actual: RunStatus | None = None) -> RunStatus:
    """Validate an UPDATE ... WHERE status=expected transition; stale writes are no-ops."""
    if actual is not None and actual != expected:
        return actual
    if new not in TRANSITIONS[expected]:
        raise ValueError(f"illegal run transition: {expected.value} -> {new.value}")
    return new
