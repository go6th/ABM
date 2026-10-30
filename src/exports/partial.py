"""The visible "Partial" note of a deliverable (workspace repair, 2026-10-31).

A file may present an area as done only when the tracker row behind it is Done (or None found: the
search ran and found nothing). When a row of the file's area is anything else (Partial, Not done,
Blocked, Not checked), the file carries a sentence that says so with the reason, in the file's own
Summary text, so a reader never takes a gap for a finding. Pure: the rows come from the tracker's
client view (``content.TrackerLine``), the areas from the platform checklist's stable row keys."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

# the checklist rows (``config/checklist.yaml``) behind each file; the same for every account
AREA_ROWS: dict[str, frozenset[str]] = {
    "people": frozenset(
        {
            "key_contact_profiles_posts",
            "people_by_platform",
            "full_profiles_senior_people",
            "posts_priority_people",
            "connections_alumni",
            "platform_vendor_account_team",
        }
    ),
    "jobs": frozenset(
        {"job_board_by_lane", "job_postings_read_classified", "job_postings_12_months"}
    ),
    "tech": frozenset({"technology_detections"}),
}
# a row in one of these states was covered: the search ran (and may have found nothing)
COVERED = frozenset({"done", "none found", "none"})
MAX_LISTED = 4


class _Line(Protocol):
    @property
    def key(self) -> str: ...
    @property
    def row(self) -> str: ...
    @property
    def status(self) -> str: ...
    @property
    def reason(self) -> str | None: ...


def partial_notes(tracker: Sequence[_Line], area: str) -> tuple[str, ...]:
    """One ``Partial: ...`` sentence for the file of ``area`` (``people``, ``jobs`` or ``tech``),
    or nothing when every row of the area is covered. Lists up to ``MAX_LISTED`` rows with their
    status and reason, then counts the rest."""
    keys = AREA_ROWS.get(area, frozenset())
    open_rows = [t for t in tracker if t.key in keys and t.status.strip().lower() not in COVERED]
    if not open_rows:
        return ()
    shown = [
        f"{t.row} ({t.status.strip().lower()}"
        + (f": {t.reason.strip().rstrip('.')}" if t.reason and t.reason.strip() else "")
        + ")"
        for t in open_rows[:MAX_LISTED]
    ]
    more = len(open_rows) - len(shown)
    tail = f", and {more} more" if more > 0 else ""
    return (
        f"Partial: this file does not cover every part of its area. {'; '.join(shown)}{tail}."
        " Do not read the absence of a row here as a finding.",
    )
