"""A refresh of part of the checklist builds on the account's previous delivered research.

A jobs, people or topic refresh searches only its scope's rows (``config/research_scopes.yaml``)
and stores only what those rows found. Its files used to be built from its own rows alone, so
they replaced the account's whole file set with a partial one: after a topic refresh Olympus
showed 0 people and 0 job postings although the research the day before had 356 and 144
(staging 2026-11-07). The build of such a run now starts from the content of the account's
previous delivered run and lays the refresh on top of it:

* findings (facts and their evidence) and news: both runs' rows, the refresh's first; a carried
  fact the refresh found again (same attribute, same value) is left out;
* tech detections: both runs' rows, the refresh's row for a technology both found;
* people and job postings: the refresh's own only when its scope searched them (the people or
  jobs rows), the previous run's otherwise; the names guard keeps every name either run knew;
* the Confirmed stack and the platform teams: the refresh's when it stored any, else carried;
* the Summary's written sections: the previous run's, which describe the whole account; a kind
  of section only the refresh wrote is added (its sections were written from its few rows);
* the pull log: both runs' calls in time order, since the files now rest on both.

The tracker is merged by the reader at row-state level (``readers.pg``), so its tally is
counted from the merged rows. Pure.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Hashable, Iterable
from functools import lru_cache
from typing import Any, TypeVar

import yaml

from account_intelligence.config import CONFIG_DIR
from exports.content import SnapshotContent

T = TypeVar("T")
SCOPES_FILE = CONFIG_DIR / "research_scopes.yaml"
# how many earlier refreshes one build may stack before it stops at the newest research
MAX_DEPTH = 8


@lru_cache(maxsize=1)
def _scope_part_rows() -> dict[str, frozenset[str]]:
    scopes = yaml.safe_load(SCOPES_FILE.read_text())["scopes"]
    return {part: frozenset(scopes.get(part) or ()) for part in ("people", "jobs")}


def scope_of(run_input: object) -> frozenset[str] | None:
    """The checklist rows a run searched when it covered only part of the checklist (its
    ``scope_rows``); None for a run of the whole checklist."""
    if not isinstance(run_input, dict):
        return None
    rows = run_input.get("scope_rows")
    if not isinstance(rows, list) or not rows:
        return None
    return frozenset(str(r) for r in rows)


def searched(scope: Collection[str], part: str) -> bool:
    """Whether a scoped run searched the rows of ``part`` (``people`` or ``jobs``)."""
    return bool(_scope_part_rows()[part] & set(scope))


def _norm(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def _unique(rows: Iterable[T], key: Callable[[T], Hashable]) -> list[T]:
    """The rows in order, without a later row whose key an earlier one had."""
    seen: set[Hashable] = set()
    out = []
    for row in rows:
        k = key(row)
        if k in seen:
            continue
        seen.add(k)
        out.append(row)
    return out


def carry_forward(
    current: SnapshotContent, previous: SnapshotContent, scope: Collection[str]
) -> SnapshotContent:
    """The content of a run that searched only ``scope``, laid over the previous delivered run's
    content (see the module docstring)."""
    found = {(f.attribute_key, _norm(f.value_text)): f.id for f in current.facts}
    # a carried fact the refresh found again is the refresh's fact: the carried sections that
    # cite it cite the refresh's row instead
    again = {
        f.id: found[(f.attribute_key, _norm(f.value_text))]
        for f in previous.facts
        if (f.attribute_key, _norm(f.value_text)) in found
    }
    carried_facts = tuple(f for f in previous.facts if f.id not in again)
    carried_ids = {f.id for f in carried_facts}
    facts = (*current.facts, *carried_facts)
    evidence = (*current.evidence, *(e for e in previous.evidence if e.fact_id in carried_ids))

    kinds = {s.kind for s in previous.sections}
    sections = (
        *(
            s.model_copy(update={"fact_ids": tuple(again.get(f, f) for f in s.fact_ids)})
            for s in previous.sections
        ),
        *(s for s in current.sections if s.kind not in kinds),
    )

    news = tuple(
        sorted(
            _unique(
                (*current.news, *previous.news),
                lambda n: (_norm(n.source_url), _norm(n.headline)),
            ),
            key=lambda n: (str(n.found_at or n.effective_date or ""), str(n.id)),
            reverse=True,
        )
    )
    detections = tuple(
        sorted(
            _unique((*current.detections, *previous.detections), lambda d: _norm(d.technology)),
            key=lambda d: (d.technology.casefold(), str(d.id)),
        )
    )

    update: dict[str, Any] = {
        "facts": facts,
        "evidence": evidence,
        # the registry is the account's (both reads list it whole); united by code all the same,
        # so a carried row never cites a code the build cannot resolve
        "sources": tuple(
            sorted(
                _unique((*current.sources, *previous.sources), lambda x: x.code),
                key=lambda x: x.code,
            )
        ),
        "sections": sections,
        "news": news,
        "detections": detections,
        "careers_host": current.careers_host or previous.careers_host,
        "entity_lock": current.entity_lock or previous.entity_lock,
        "sense_check": current.sense_check or previous.sense_check,
        "pull_calls": tuple(
            sorted((*previous.pull_calls, *current.pull_calls), key=lambda c: c.at)
        ),
        "pull_failed": current.pull_failed + previous.pull_failed,
    }
    if not searched(scope, "people"):
        update.update(
            people=previous.people,
            people_counts=previous.people_counts,
            findings=previous.findings,
        )
    # every name either run knew stays guarded the stricter way unless one of them listed it
    names: dict[str, bool] = {}
    for name, listed in (*previous.people_names, *current.people_names):
        names[name] = names.get(name, False) or listed
    update["people_names"] = tuple(sorted(names.items()))
    if not searched(scope, "jobs"):
        update.update(postings=previous.postings, jobs_counts=previous.jobs_counts)
    if not current.stack:
        update["stack"] = previous.stack
    if not current.teams:
        update.update(
            teams=previous.teams,
            teams_matching=previous.teams_matching,
            teams_since=previous.teams_since,
        )

    counts = dict(current.snapshot_counts)
    sizes = {
        "facts": len(facts),
        "fact_evidence": len(evidence),
        "report_sections": len(sections),
        "news_events": len(news),
        "tech_detections": len(detections),
        "tech_stack_items": len(update.get("stack", current.stack)),
        "platform_teams": len(update.get("teams", current.teams)),
    }
    counts.update({k: v for k, v in sizes.items() if k in counts})
    update["snapshot_counts"] = counts
    return current.model_copy(update=update)
