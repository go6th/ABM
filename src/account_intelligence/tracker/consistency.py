"""Tracker consistency rules (master prompt v2, 2026-10-24, section 11). Pure.

A coverage row may not contradict the content delivered beside it: a None news row next to a
dated news table, a None leadership row next to a recent appointment, a Done financials row for
a private company, a customers row that nobody could match because no reference list was ever
supplied, a pass 1 row Blocked for a reason only the account owner can lift. The rules run on
the client view (status words and client reason text) where the tracker lines are read for a
client file, and the tally is recomputed from the rows afterwards (T11), so a count never
disagrees with the table it stands above. Every word comes from ``config/tracker_v1.yaml``
``consistency`` and ``reason_codes``; an empty ``consistency`` block applies no rule.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from account_intelligence.tracker.completion import RowState
from account_intelligence.tracker.config import load_checklist, load_tracker_config
from account_intelligence.tracker.model import Status
from account_intelligence.tracker.render import STATUS_TEXT
from account_intelligence.tracker.tally import tally

_STATUS_OF_TEXT = {text: Status(key) for key, text in STATUS_TEXT.items() if key != "unset"}


@dataclass(frozen=True)
class CoverageLine:
    """One client-view row: the words the client sees, never a code or a count typed by hand."""

    key: str
    row: str
    status: str
    priority: str
    reason: str | None
    tools: tuple[str, ...] = ()
    results: int | None = None


@dataclass(frozen=True)
class ContentSignals:
    """What the rules need to know about the content beside the tracker."""

    dated_news: bool = False
    recent_leadership_change: bool = False
    is_public: bool | None = None
    reference_list_supplied: bool = False


def apply_rules(
    lines: Sequence[CoverageLine],
    signals: ContentSignals,
    rules: Mapping[str, Any] | None = None,
    reason_texts: Mapping[str, str] | None = None,
    passes: Mapping[str, int] | None = None,
) -> tuple[CoverageLine, ...]:
    """The lines with every section 11 rule applied, in the same order."""
    cfg = load_tracker_config()
    rules = cfg.consistency if rules is None else rules
    if not rules:
        return tuple(lines)
    reason_texts = cfg.reason_codes if reason_texts is None else reason_texts
    passes = {r.key: r.pass_no for r in load_checklist().rows} if passes is None else passes
    no_access = reason_texts.get("no_internal_access")
    none, partial = STATUS_TEXT["none"], STATUS_TEXT["partial"]
    blocked, not_done = STATUS_TEXT["blocked"], STATUS_TEXT["not_done"]
    private_rows = set(rules.get("private_rows") or ())
    out: list[CoverageLine] = []
    for line in lines:
        if line.key == rules.get("news_row") and line.status == none and signals.dated_news:
            line = replace(line, status=partial, reason=str(rules["news_reason"]))
        if (
            line.key == rules.get("leadership_row")
            and line.status == none
            and signals.recent_leadership_change
        ):
            line = replace(line, status=partial, reason=str(rules["leadership_reason"]))
        if signals.is_public is False and line.key in private_rows:
            line = replace(line, status=none, reason=str(rules["private_reason"]))
        if line.key == rules.get("reference_row") and not signals.reference_list_supplied:
            line = replace(line, status=blocked, reason=str(rules["reference_needs"]))
        if passes.get(line.key) == 1 and no_access and line.reason == no_access:
            line = replace(line, status=not_done, reason=str(rules["no_internal_access_needs"]))
        out.append(line)
    return tuple(out)


def row_states(lines: Sequence[CoverageLine]) -> tuple[RowState, ...]:
    """The lines as the tally sees them (status word back to the status)."""
    return tuple(
        RowState(
            key=line.key,
            priority=line.priority.lower(),
            status=_STATUS_OF_TEXT.get(line.status),
            reason_code=None,
            proof_hash=None,
            swept=False,
        )
        for line in lines
    )


def tally_sentences(lines: Sequence[CoverageLine]) -> tuple[str, ...]:
    """The tally recomputed from the (adjusted) rows, every figure with its base."""
    return tuple(tally(row_states(lines))["sentences"])
