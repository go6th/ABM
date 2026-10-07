"""Weekly digest assembly (AL4): bounded and deterministic. Items are ordered by score (high
first), then signal time, then fingerprint; at most ``limit`` items, the rest counted as "and N
more in the app". Sensitive items keep their fixed wording; Leads keep their label.

GUARANTEE(test_digests_are_bounded_and_deterministic)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from alerts import lint


@dataclass(frozen=True)
class Item:
    """One digest line's inputs (already rendered by ``alerts.templates``)."""

    fingerprint: str
    score: int
    signal_at: datetime
    line: str


def assemble(items: Sequence[Item], *, limit: int) -> tuple[list[str], int]:
    """The lines and the count left out."""
    ordered = sorted(items, key=lambda i: (-i.score, i.signal_at, i.fingerprint))
    seen: set[str] = set()
    lines: list[str] = []
    for it in ordered:  # LOOP(digest assembly): cap=len(items); exit=end
        if it.fingerprint in seen:
            continue
        seen.add(it.fingerprint)
        lines.append(lint.check(it.line))
    shown = lines[:limit]
    return shown, len(lines) - len(shown)
