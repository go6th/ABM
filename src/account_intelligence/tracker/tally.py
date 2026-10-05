"""Tally and completeness, recomputed from rows on every read (T11). Pure.

No function here accepts a count: each is derived from the rows given and the checklist. Every
rendered count names its base ("15 of 19 High rows Done").
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

from account_intelligence.tracker.completion import RowState
from account_intelligence.tracker.config import load_checklist
from account_intelligence.tracker.model import Status

_ORDER = ("done", "partial", "not_done", "blocked", "none", "unset")


def tally(rows: Sequence[RowState]) -> dict[str, Any]:
    """Status counts overall and per priority, with the checklist bases."""
    checklist = load_checklist()
    bases = {"high": 0, "medium": 0, "optional": 0}
    for row in checklist.rows:
        bases[row.priority] += 1
    counts = dict.fromkeys(_ORDER, 0)
    by_priority = {p: dict.fromkeys(_ORDER, 0) for p in bases}
    for r in rows:
        status = r.status.value if r.status else "unset"
        counts[status] += 1
        by_priority.setdefault(r.priority, dict.fromkeys(_ORDER, 0))[status] += 1
    high_done = by_priority["high"]["done"]
    return {
        "status_counts": counts,
        "by_priority": by_priority,
        "total_rows": len(rows),
        "total_base": sum(bases.values()),
        "high_base": bases["high"],
        "medium_base": bases["medium"],
        "optional_base": bases["optional"],
        "high_done": high_done,
        "high_rows_done_str": f"{high_done} of {bases['high']} High rows Done",
        "sentences": tally_sentences(by_priority, bases),
    }


def tally_sentences(by_priority: dict[str, dict[str, int]], bases: dict[str, int]) -> list[str]:
    """One sentence per priority, every figure with its base."""
    out = []
    for priority, base in bases.items():
        c = by_priority.get(priority, {})
        out.append(
            f"{priority.capitalize()} rows: {c.get('done', 0)} Done, {c.get('none', 0)} None,"
            f" {c.get('partial', 0)} Partial, {c.get('blocked', 0)} Blocked,"
            f" {c.get('not_done', 0)} Not done, {c.get('unset', 0)} not yet reported,"
            f" of {base}"
        )
    return out


def completeness(rows: Sequence[RowState]) -> tuple[float, int]:
    """High rows Done or None over High rows, with the base."""
    high = [r for r in rows if r.priority == "high"]
    ok = sum(r.status in (Status.DONE, Status.NONE) for r in high)
    return (ok / len(high)) if high else 1.0, len(high)


T = TypeVar("T", bound=RowState)


def sort_for_rendering(rows: Sequence[T]) -> Sequence[T]:
    """Open work first (unset, Not done, Blocked, Partial), then priority, then checklist order."""
    order = {r.key: i for i, r in enumerate(load_checklist().rows)}
    rank = {"high": 0, "medium": 1, "optional": 2}

    def key(r: T) -> tuple[int, int, int, str]:
        is_open = r.status in (None, Status.NOT_DONE, Status.BLOCKED, Status.PARTIAL)
        return (0 if is_open else 1, rank.get(r.priority, 3), order.get(r.key, 999), r.key)

    return sorted(rows, key=key)
