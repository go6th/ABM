"""Link the archive records of one account that report the same event (archive record, step 6).

Two records report the same event when they name the same person for the same kind of change and
the effective dates are at most three days apart. The best source is the master: a primary source
first, then a better grade, then the earlier publication. The others get ``duplicate_of`` (their
text is the master's, a syndication) or ``corroborates`` (a different account of the same event).
An AI summary or aggregator item whose original release is among the records gets the release's
address in ``derived_from``; the master's events carry the grade of the master (a Confirmed event
stays Confirmed, the summary's stay Lead).

Pure: records in, the records that changed out. Nothing is fetched and nothing is searched; an
original that is not among the account's records stays ``Not found``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from account_intelligence import archive_facts as AF
from account_intelligence.archive_clean import words_of

_CHANGE_EVENT = {
    "Appointed": "Appointment",
    "Promoted": "Appointment",
    "Retiring": "Departure/Retirement",
    "Departed": "Departure/Retirement",
}
_RANK = {"Confirmed": 0, "Probable": 1, "Lead": 2}
SAME_DAYS = 3
COPY_SIMILARITY = 0.8


def record_id(domain: str, record: Mapping[str, Any]) -> str:
    """The id the download gives a record: ``arc_<domain>_<12 hex of its content hash>``."""
    digest = str(record.get("content_hash") or "").split(":")[-1]
    if not digest:
        digest = hashlib.sha256(
            json.dumps(record, sort_keys=True, default=str).encode()
        ).hexdigest()
    return f"arc_{domain}_{digest[:12]}"


def _changes(record: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """``(event type, person, effective date)`` of each person change the record states."""
    out: list[tuple[str, str, str]] = []
    for person in record.get("people") or []:
        etype = _CHANGE_EVENT.get(str(person.get("change_type")))
        name = " ".join(str(person.get("name", "")).lower().split())
        if etype and name:
            out.append((etype, name, str(person.get("effective_date") or AF.NOT_STATED)))
    return out


def same_event(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """True when the records state one person change of the same kind within ``SAME_DAYS``."""
    for etype, name, when in _changes(a):
        for etype2, name2, when2 in _changes(b):
            if etype == etype2 and name == name2 and AF.within_days(when, when2, SAME_DAYS):
                return True
    return False


def _similar(a: str, b: str) -> bool:
    wa, wb = set(words_of(a)), set(words_of(b))
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= COPY_SIMILARITY


def _best(record: Mapping[str, Any]) -> tuple[Any, ...]:
    """Sort key of a master: primary, then grade, then not AI-generated, then the earlier date."""
    return (
        not record.get("is_primary"),
        _RANK.get(str(record.get("fact_grade")), 3),
        bool(record.get("is_ai_generated")),
        str(record.get("published_date") or "9999"),
    )


def reconcile(domain: str, records: Mapping[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The records (by address) whose links changed, as copies with the links set. The input is not
    changed."""
    urls = sorted(records)
    parent = {u: u for u in urls}

    def find(u: str) -> str:
        # LOOP(union-find root): cap=the number of records (path halving); exit=a root is reached
        while parent[u] != u:
            parent[u] = parent[parent[u]]
            u = parent[u]
        return u

    for i, a in enumerate(urls):
        for b in urls[i + 1 :]:
            if same_event(records[a], records[b]):
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for u in urls:
        groups.setdefault(find(u), []).append(u)
    changed: dict[str, dict[str, Any]] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        master_url = min(members, key=lambda u: _best(records[u]))
        master = records[master_url]
        master_id = record_id(domain, master)
        for u in members:
            if u == master_url:
                continue
            rec = json.loads(json.dumps(records[u]))
            link = (
                "duplicate_of"
                if _similar(rec.get("body_markdown", ""), master.get("body_markdown", ""))
                else "corroborates"
            )
            if link == "duplicate_of":
                rec["duplicate_of"] = master_id
            else:
                rec["corroborates"] = sorted({*rec.get("corroborates", []), master_id})
            derived = rec.get("derived_from")
            if (
                isinstance(derived, dict)
                and derived.get("url") == AF.NOT_FOUND
                and master.get("is_primary")
                and not master.get("is_ai_generated")
            ):
                derived["url"] = master.get("url", AF.NOT_FOUND)
                rec.setdefault("quality", {})["primary_matched"] = True
            if rec != records[u]:
                changed[u] = rec
    return changed
