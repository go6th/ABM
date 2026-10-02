from __future__ import annotations

from collections.abc import Sequence

"""Tracker tally logic."""


from typing import Any

from account_intelligence.tracker.completion import RowState
from account_intelligence.tracker.config import load_checklist_config
from account_intelligence.tracker.model import Status


def tally(rows: Sequence[RowState]) -> dict[str, Any]:
    checklist, _ = load_checklist_config()

    counts: dict[str, int] = {
        "done": 0,
        "partial": 0,
        "not_done": 0,
        "blocked": 0,
        "none": 0,
        "unset": 0,
    }
    priorities = {"high": 0, "medium": 0, "optional": 0}

    # Pre-compute bases
    priority_bases = {"high": 0, "medium": 0, "optional": 0}
    for crow in checklist.rows:
        p = crow.get("priority", "optional").lower()
        if p in priority_bases:
            priority_bases[p] += 1

    total_base = sum(priority_bases.values())

    high_done = 0

    for r in rows:
        if r.status:
            counts[r.status.value] += 1
        else:
            counts["unset"] += 1

        if r.priority in priorities:
            priorities[r.priority] += 1

        if r.priority == "high" and r.status == Status.DONE:
            high_done += 1

    return {
        "status_counts": counts,
        "total_rows": len(rows),
        "total_base": total_base,
        "high_base": priority_bases["high"],
        "medium_base": priority_bases["medium"],
        "optional_base": priority_bases["optional"],
        "high_done": high_done,
        "high_rows_done_str": f"{high_done} of {priority_bases['high']} High rows Done",
    }


def completeness(rows: Sequence[RowState]) -> tuple[float, int]:
    high_rows = 0
    high_done_or_none = 0
    for r in rows:
        if r.priority == "high":
            high_rows += 1
            if r.status in (Status.DONE, Status.NONE):
                high_done_or_none += 1
    return (high_done_or_none / high_rows) if high_rows > 0 else 1.0, high_rows


from typing import TypeVar

T = TypeVar("T", bound=RowState)


def sort_for_rendering(rows: Sequence[T]) -> Sequence[T]:
    """Sort order: open work first (unset, not_done, blocked, partial) by priority then checklist order."""
    checklist, _ = load_checklist_config()
    checklist_order = {r["key"]: i for i, r in enumerate(checklist.rows)}

    def sort_key(r: T) -> tuple[int, int, int]:
        is_open = r.status in (None, Status.NOT_DONE, Status.BLOCKED, Status.PARTIAL)
        prio = 0 if r.priority == "high" else (1 if r.priority == "medium" else 2)
        idx = checklist_order.get(r.key, 999)
        return (0 if is_open else 1, prio, idx)

    return sorted(rows, key=sort_key)
