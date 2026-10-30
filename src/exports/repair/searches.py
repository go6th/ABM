"""The Web searches table: one row per distinct query, however many providers and runs asked it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from exports.repair import core
from exports.repair.core import Exclusion, RepairedTable, Sheet, WorkspaceProfile
from exports.repair.keys import norm_query
from exports.repair.records import SearchCall


@dataclass
class _Query:
    display: str
    calls: list[SearchCall] = field(default_factory=list)


def _first_run(calls: Sequence[SearchCall]) -> date | None:
    runs = [c.run_at.date() for c in calls if c.run_at]
    return min(runs) if runs else None


def _used(calls: Sequence[SearchCall]) -> str:
    """Y when any run used the results, N only when every run is known not to have, else Unknown."""
    if any(c.used is True for c in calls):
        return "Y"
    if calls and all(c.used is False for c in calls):
        return "N"
    return core.UNKNOWN


def _sites(calls: Sequence[SearchCall]) -> str:
    seen: dict[str, str] = {}
    for c in calls:
        for site in c.sites:
            text = site.strip()
            if text:
                seen.setdefault(text.lower(), text)
    return ", ".join(seen[k] for k in sorted(seen)) or core.NOT_FOUND


# the provider code is an internal identifier: a client file says "6th agents"
PROVIDER_LABEL = "6th agents"


def build_searches(profile: WorkspaceProfile, calls: Sequence[SearchCall]) -> RepairedTable:
    """One row per normalised query (the first-seen wording is shown). Providers and sites are
    unique and sorted, ``Runs`` counts the calls, ``Pages returned (total)`` adds them up. A call
    with no query is dropped and logged. Rows run by first run, unknown last, then by query."""
    exclusions: list[Exclusion] = []
    queries: dict[str, _Query] = {}
    for call in calls:
        key = norm_query(call.query)
        if not key:
            exclusions.append(Exclusion("searches", "", "empty query"))
            continue
        queries.setdefault(key, _Query(" ".join(call.query.split()))).calls.append(call)

    built: list[tuple[date | None, str, dict[str, str]]] = []
    for key, q in queries.items():
        providers = sorted({c.provider.strip() for c in q.calls if c.provider.strip()})
        first = _first_run(q.calls)
        purpose = next((c.purpose.strip() for c in q.calls if c.purpose.strip()), "")
        built.append(
            (
                first,
                key,
                {
                    "Query": q.display,
                    "Purpose (Coverage item #)": purpose or core.UNKNOWN,
                    "Providers": PROVIDER_LABEL if providers else core.NOT_FOUND,
                    "First run": core.iso(first),
                    "Runs": str(len(q.calls)),
                    "Pages returned (total)": str(sum(max(0, c.pages) for c in q.calls)),
                    "Sites": _sites(q.calls),
                    "Used (Y/N)": _used(q.calls),
                },
            )
        )
    built.sort(key=lambda b: (b[0] is None, b[0] or date.min, b[1]))
    valid = len(calls) - len(exclusions)
    stats = {
        "calls_in": len(calls),
        "unique_queries": len(queries),
        "repeats_removed": valid - len(queries),
        "dropped_empty": len(exclusions),
    }
    return RepairedTable(
        "searches",
        "Web searches",
        [Sheet("Web searches", core.SEARCH_COLUMNS, [b[2] for b in built])],
        exclusions,
        stats,
    )
