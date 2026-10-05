"""The single pre-call decision (section 4.2). Pure: no I/O, no clock, no connector import.

The connector ledger calls ``decide`` inside the claim transaction, after a replay has been
answered (a replay costs nothing, K8) and before the atomic reserve (the reserve stays the only
place spend is incremented and the final word on the cap):

1. an operation proven free (``free_operations`` with a source) passes without a cap (K14),
   unless it once returned a cost: then it is a defect and refused ``free_defect`` from then on;
2. a run created without the cap requirement (rows from before migration 0015) keeps the legacy
   budget rule;
3. no approved cap: refused ``no_cap`` (K2);
4. a recorded overshoot stops every further paid call (K4);
5. an operation with no cost figure is refused unless the approved cap carries a sample line for
   it with sample calls left (K5: one bounded sample round, then a new approval);
6. an estimate above what remains is refused ``cap_reached`` with the remaining amount (the
   atomic reserve would refuse it too; this names the reason before the call).

GUARANTEE(test_gate_refuses_without_an_approved_cap): K2.
GUARANTEE(test_gate_refuses_unknown_cost_without_a_sample_line): K5.
GUARANTEE(test_gate_stops_after_an_overshoot): K4.
GUARANTEE(test_gate_blocks_a_free_operation_that_charged): K14.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Reason = Literal[
    "free", "free_defect", "legacy", "allowed", "no_cap", "overshoot", "unknown_cost", "cap_reached"
]


@dataclass(frozen=True)
class RunCapState:
    """The enforcement columns of a run, as read in the claim transaction."""

    cap_required: bool
    cap_version: int | None
    budget_micros: int | None
    spent_micros: int
    overshoot_micros: int
    sample_calls_left: dict[str, int]  # op key -> sample calls the approved cap still allows
    defective_free_ops: frozenset[str] = frozenset()  # "free" operations that returned a cost


@dataclass(frozen=True)
class Decision:
    """Allow or refuse, with the reason and what remains of the cap (micro-dollars)."""

    allowed: bool
    reason: Reason
    remaining_micros: int | None = None


def decide(
    state: RunCapState,
    op_key: str,
    estimate_micros: int,
    cost_known: bool,
    free_operations: frozenset[str],
) -> Decision:
    """The decision for one paid call (see the module docstring)."""
    if estimate_micros < 0:
        raise ValueError("an estimate cannot be negative")
    if op_key in free_operations:
        if op_key in state.defective_free_ops:
            return Decision(False, "free_defect")
        return Decision(True, "free")
    if not state.cap_required:
        return Decision(True, "legacy")
    if state.cap_version is None or state.budget_micros is None:
        return Decision(False, "no_cap")
    remaining = state.budget_micros - state.spent_micros
    if state.overshoot_micros > 0:
        return Decision(False, "overshoot", remaining)
    if not cost_known and state.sample_calls_left.get(op_key, 0) <= 0:
        return Decision(False, "unknown_cost", remaining)
    if estimate_micros > remaining:
        return Decision(False, "cap_reached", remaining)
    return Decision(True, "allowed", remaining - estimate_micros)
