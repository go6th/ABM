"""Diff-first Summary (RF7): the section that opens a refreshed Summary, rendered from the delta
by template (no model). Sensitive items use the D-76 templates; every line names its source
code; an empty delta says what was checked and when. The text passes the wording lint.

GUARANTEE(test_the_summary_opens_with_the_rendered_delta)
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from alerts import lint, sensitive
from refresh.delta import Delta, DeltaItem

_KIND_TITLE = {
    "new": "New",
    "regraded": "Regraded",
    "expired": "Expired (moved to history)",
    "count_change": "Counts",
}


def _line(it: DeltaItem, labels: Mapping[str, str], event_types: Mapping[str, str]) -> str:
    cat = sensitive.category(event_types.get(it.identity, ""))
    src = f" [{it.source_code}]" if it.source_code else ""
    if cat is not None:
        return f"- {sensitive.render(cat, it.source_code)}"
    label = labels.get(it.identity, it.identity)
    if it.kind == "new":
        return f"- {label} ({it.after or 'ungraded'}){src}"
    if it.kind == "regraded":
        return f"- {label}: {it.before} to {it.after}{src}"
    if it.kind == "expired":
        return f"- {label} (was {it.before}){src}"
    return f"- {label}: {it.before} to {it.after} (base: {it.base})"


def render(
    delta: Delta,
    *,
    since_version: int,
    since_date: date,
    labels: Mapping[str, str] | None = None,
    event_types: Mapping[str, str] | None = None,
) -> str:
    """The "What changed" section."""
    head = f"What changed since version {since_version} ({since_date.isoformat()})"
    if delta.no_change:
        checked = ", ".join(delta.checked) or "nothing"
        body = f"No change. Checked: {checked}, on {delta.checked_at.date().isoformat()}."
        return lint.check(f"{head}\n\n{body}\n")
    parts = [head, ""]
    for kind in ("new", "regraded", "expired", "count_change"):
        rows = [i for i in delta.items if i.kind == kind]
        if rows:
            parts.append(_KIND_TITLE[kind])
            parts += [_line(i, labels or {}, event_types or {}) for i in rows]
            parts.append("")
    return lint.check("\n".join(parts))
