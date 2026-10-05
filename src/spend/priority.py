"""Funding under scarcity (K10). Pure and deterministic.

Pending operations are ranked High, then Medium, then Optional (``assets/checklist.yaml``
priority), then by stage order, row key, operation and cost (a fixed, total tie-break). Funding
walks that order and takes each operation whose upper-bound cost fits what remains; an operation
that does not fit is left unfunded and the walk continues (a cheaper lower row may still fit, but a
lower row is never funded while a higher row it could afford instead is skipped). The total funded
never exceeds the remaining amount. Every unfunded row is returned, to be Blocked ``cap_reached``
by the caller; nothing is dropped.

GUARANTEE(test_priority_never_exceeds_remaining_and_prefers_higher_rows): see the property test.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

PRIORITY_RANK = {"High": 0, "Medium": 1, "Optional": 2}


@dataclass(frozen=True)
class Candidate:
    """One pending operation with its upper-bound cost (micro-dollars)."""

    row_key: str
    priority: str
    stage_index: int
    operation: str
    cost_micros: int


@dataclass(frozen=True)
class Funding:
    """The funded and unfunded candidates and what remains."""

    funded: tuple[Candidate, ...]
    unfunded: tuple[Candidate, ...]
    remaining_micros: int

    @property
    def unfunded_rows(self) -> tuple[str, ...]:
        """Row keys with at least one unfunded operation, in rank order, without repeats."""
        return tuple(dict.fromkeys(c.row_key for c in self.unfunded))


def rank_key(c: Candidate) -> tuple[int, int, str, str, int]:
    """The fixed, total order: priority, stage, row key, operation, then cost (cheaper first), so
    the result never depends on the input order."""
    return (
        PRIORITY_RANK.get(c.priority, len(PRIORITY_RANK)),
        c.stage_index,
        c.row_key,
        c.operation,
        c.cost_micros,
    )


def fund(candidates: Sequence[Candidate], remaining_micros: int) -> Funding:
    """Fund in rank order within ``remaining_micros`` (negative remaining funds nothing)."""
    if any(c.cost_micros < 0 for c in candidates):
        raise ValueError("a cost cannot be negative")
    left = max(remaining_micros, 0)
    funded: list[Candidate] = []
    unfunded: list[Candidate] = []
    for c in sorted(candidates, key=rank_key):
        if c.cost_micros <= left:
            funded.append(c)
            left -= c.cost_micros
        else:
            unfunded.append(c)
    return Funding(tuple(funded), tuple(unfunded), left)


def fund_indexed(
    candidates: Sequence[Candidate], remaining_micros: int
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """``fund`` over a batch whose entries may repeat: (funded, unfunded) positions in the input,
    funded positions in rank order (the input position is the last tie-break), unfunded in input
    order. Same rule as ``fund``: never exceeds the remaining amount."""
    if any(c.cost_micros < 0 for c in candidates):
        raise ValueError("a cost cannot be negative")
    left = max(remaining_micros, 0)
    funded: list[int] = []
    for i in sorted(range(len(candidates)), key=lambda k: (rank_key(candidates[k]), k)):
        if candidates[i].cost_micros <= left:
            funded.append(i)
            left -= candidates[i].cost_micros
    chosen = set(funded)
    return tuple(funded), tuple(i for i in range(len(candidates)) if i not in chosen)
