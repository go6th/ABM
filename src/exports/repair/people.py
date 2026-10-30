"""The People table and its Leadership sheet (spec: Tables, section 2). Pure and account-neutral:
the account's domains, entities, lanes, title exclusions and consent come from the
``WorkspaceProfile``; the level, lane and priority rules are the shared ones in
``parity.rules``.

Rows come only from profile or people-search data. A person extracted from news text is never a
People row, a name is a validated person name (a descriptive phrase is split into the name and
the title it carried), duplicates merge on the profile URL and on name plus employer, employers
that are not the account's are dropped, and the profile's title exclusions are applied. Every
cell is filled or carries an explicit placeholder.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from exports.repair import core
from exports.repair.keys import canonical_url, fold, host_of, norm_name, registrable
from exports.repair.names import (
    is_person_name,
    is_profile_name,
    normalise_profile_name,
    parse_person_name,
)
from exports.repair.records import LeadershipRecord, PersonRecord
from parity import rules

TRUSTED_ORIGINS = ("profile", "people_search", "import")
# origins that prove people data was collected with consent (an import is the user's own file)
CONSENT_ORIGINS = ("profile", "people_search")
# a stored title that says nothing: replaced by the descriptive text found next to the name
GENERIC_TITLES = (
    "", "employee", "member", "professional", "staff", "team member", "linkedin member",
    "n/a", "na", "none", "unknown", "-", "--",
)  # fmt: skip
CONSENT_REASON = "people data consent needed"
NO_LEADERSHIP_REASON = "no press releases or filings were read for leadership"
_PRIORITY_ORDER = {"A": 0, "B": 1, "C": 2}
_CORPORATE = re.compile(
    r"\b(?:inc|incorporated|corp|corporation|co|company|ltd|limited|llc|plc|gmbh|ag|sa|bv|nv|the)\b"
)
_CANONICAL_STATUS = {s.casefold(): s for s in core.LEADERSHIP_STATUSES}


# ---------------------------------------------------------------------------- small helpers
def _clean(value: str | None) -> str:
    """The text with spaces collapsed; an explicit placeholder reads back as empty so a table that
    is fed back in builds the same table."""
    text = " ".join((value or "").split())
    return "" if core.is_placeholder(text) else text


def _norm_employer(text: str) -> str:
    plain = re.sub(r"[^a-z0-9 ]", " ", fold(text))
    return " ".join(_CORPORATE.sub(" ", plain).split())


def _host_or_domain(value: str) -> str:
    raw = value.strip().lower()
    if "/" in raw or "@" in raw:
        return registrable(host_of(raw if "://" in raw else "https://" + raw.split("@")[-1]))
    return registrable(raw.removeprefix("www."))


def _account_domains(profile: core.WorkspaceProfile) -> frozenset[str]:
    return frozenset(_host_or_domain(d) for d in profile.domains if d)


def _linkedin(url: str) -> str:
    """The canonical profile URL when it is a linkedin.com /in/ or /pub/ page, else ``""``."""
    canon = canonical_url(url)
    if not canon:
        return ""
    parts = urlsplit(canon)
    host = parts.hostname or ""
    if registrable(host) != "linkedin.com" or not re.match(r"^/(?:in|pub)/[^/]+", parts.path, re.I):
        return ""
    return urlunsplit(("https", host, parts.path.rstrip("/"), "", ""))


def _url_key(url: str) -> str:
    """The de-duplication key of a profile URL (a LinkedIn page ignores its country sub-domain)."""
    linked = _linkedin(url)
    if linked:
        return "linkedin.com" + urlsplit(linked).path.lower()
    return canonical_url(url)


def _phrase(phrase: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in phrase.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


def _title_exclusion(profile: core.WorkspaceProfile) -> list[tuple[str, re.Pattern[str]]]:
    return [(p, _phrase(p)) for p in profile.title_exclusions if p and p.strip()]


def _names_platform(profile: core.WorkspaceProfile, title: str) -> bool:
    """The title names a lane platform: the profile's dictionary, else the shared keyword rule."""
    if not title:
        return False
    platforms = profile.platforms
    for lane in getattr(platforms, "lanes", ()) or ():
        try:
            if re.search(lane.pattern, title, lane.flags):
                return True
        except re.error:
            continue
    return rules.names_platform(title)


def _resolve_name(raw: str, *, structured: bool = False) -> tuple[str, str] | None:
    """``(name, remainder)`` for a valid name (remainder ``""``) or a descriptive phrase that
    starts with one; ``None`` when neither. ``structured`` is a provider's own name field
    (profile, people search, import): a nickname or credentials are dropped from it and a
    short surname or a trailing initial is accepted."""
    text = _clean(raw)
    if is_person_name(text):
        return text, ""
    found = parse_person_name(text)
    if found is not None or not structured:
        return found
    plain = normalise_profile_name(text)
    return (plain, "") if is_profile_name(plain) else None


def _dedupe_clusters(keys: Sequence[tuple[str, ...]]) -> list[list[int]]:
    """Group indexes that share any key (transitively), keeping first-seen order."""
    parent = list(range(len(keys)))

    def find(i: int) -> int:
        # LOOP(union-find root): cap=the depth of the cluster; exit=the root
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[str, int] = {}
    for i, ks in enumerate(keys):
        for k in ks:
            if k in owner:
                a, b = find(i), find(owner[k])
                if a != b:
                    parent[max(a, b)] = min(a, b)
            else:
                owner[k] = i
    groups: dict[int, list[int]] = {}
    for i in range(len(keys)):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: g[0])


# ---------------------------------------------------------------------------- People rows
@dataclass
class _Person:
    key: str
    name: str
    title: str
    headline: str
    level: str
    lanes: list[str]
    priorities: list[str]
    url: str
    location: str
    employer: str
    employer_domain: str
    found_by: str
    former_si: str
    last_verified: date | None
    note: str
    merged: list[str] = field(default_factory=list)

    def absorb(self, other: _Person) -> None:
        """Keep this row's fields; fill the empty ones from ``other``."""
        for attr in (
            "title", "headline", "level", "url", "location", "employer", "employer_domain",
            "found_by", "former_si", "last_verified", "note",
        ):  # fmt: skip
            if not getattr(self, attr) and getattr(other, attr):
                setattr(self, attr, getattr(other, attr))
        for lane_name in other.lanes:
            if lane_name not in self.lanes:
                self.lanes.append(lane_name)
        self.priorities.extend(other.priorities)
        self.merged.append(other.key)


def _level(title: str, stored: str) -> tuple[int, str]:
    number, label = rules.level(title)
    if number == len(rules.LEVELS) and stored:
        for i, known in enumerate(rules.LEVELS, start=1):
            if fold(known) == fold(stored):
                return i, known
    return number, label


def _best_priority(priorities: Sequence[str], title: str) -> str:
    valid = [p for p in priorities if p in _PRIORITY_ORDER]
    if valid:
        return min(valid, key=_PRIORITY_ORDER.__getitem__)
    return rules.priority(title) if title else "C"


def _entity(
    profile: core.WorkspaceProfile, accounts: frozenset[str], row: _Person
) -> core.EntityRule | None:
    """The non-account-label entity rule the employer text matches (``None``: the account)."""
    if row.employer_domain and _host_or_domain(row.employer_domain) in accounts:
        return None
    for rule in profile.entities:
        for pattern in rule.patterns:
            try:
                if row.employer and re.search(pattern, row.employer, re.IGNORECASE):
                    return rule
            except re.error:
                continue
    return None


def _consent_row() -> dict[str, str]:
    row = dict.fromkeys(core.PEOPLE_COLUMNS, core.NOT_APPLICABLE)
    row["Name"] = core.not_collected(CONSENT_REASON)
    return row


def _placeholder_row(columns: tuple[str, ...], reason: str) -> dict[str, str]:
    row = dict.fromkeys(columns, core.NOT_APPLICABLE)
    row["Name"] = core.not_collected(reason)
    return row


def _build_people_rows(
    profile: core.WorkspaceProfile,
    people: Sequence[PersonRecord],
    departed: dict[str, str],
    current: frozenset[str],
    exclusions: list[core.Exclusion],
    stats: dict[str, Any],
    notes: list[str],
) -> list[dict[str, str]]:
    stats.update(
        dropped_news=0, dropped_flagged=0, dropped_invalid_name=0, dropped_other_employer=0,
        dropped_title_excluded=0, dropped_duplicates=0, dropped_no_consent=0,
        non_account_entity=0, departed_flagged=0, consent_missing=False,
    )  # fmt: skip
    trusted: list[PersonRecord] = []
    for rec in people:
        if rec.origin not in TRUSTED_ORIGINS:
            stats["dropped_news"] += 1
            exclusions.append(core.Exclusion("people", rec.key, "sourced from news text", rec.name))
        else:
            trusted.append(rec)

    if not profile.people_consent and not any(r.origin in CONSENT_ORIGINS for r in trusted):
        stats["consent_missing"] = True
        stats["dropped_no_consent"] = len(trusted)
        for rec in trusted:
            exclusions.append(core.Exclusion("people", rec.key, CONSENT_REASON, rec.name))
        notes.append("People data consent needed: no people were collected for this workspace.")
        return [_consent_row()]

    accounts = _account_domains(profile)
    title_rules = _title_exclusion(profile)
    kept: list[_Person] = []
    for rec in trusted:
        if rec.excluded or rec.internal_only:
            stats["dropped_flagged"] += 1
            why = "marked excluded" if rec.excluded else "internal only"
            exclusions.append(core.Exclusion("people", rec.key, why, rec.name))
            continue
        resolved = _resolve_name(rec.name, structured=True)
        if resolved is None:
            stats["dropped_invalid_name"] += 1
            exclusions.append(
                core.Exclusion("people", rec.key, "not a valid person name", rec.name)
            )
            continue
        name, remainder = resolved
        title = _clean(rec.title)
        headline = _clean(rec.headline)
        if title.casefold() in GENERIC_TITLES:
            title = remainder or headline
        domain = _clean(rec.employer_domain)
        if domain and _host_or_domain(domain) not in accounts:
            stats["dropped_other_employer"] += 1
            exclusions.append(
                core.Exclusion("people", rec.key, "employer is not the account", domain)
            )
            continue
        hit = next((p for p, rx in title_rules if rx.search(title)), "")
        if hit:
            stats["dropped_title_excluded"] += 1
            exclusions.append(core.Exclusion("people", rec.key, f"title excluded: {hit}", title))
            continue
        kept.append(
            _Person(
                key=rec.key, name=name, title=title, headline=headline, level=_clean(rec.level),
                lanes=[x for x in (_clean(v) for v in rec.lanes) if x],
                priorities=[rec.priority.strip().upper()], url=_clean(rec.profile_url),
                location=_clean(rec.location), employer=_clean(rec.employer),
                employer_domain=domain, found_by=_clean(rec.found_by),
                former_si=_clean(rec.former_si), last_verified=rec.last_verified,
                note=_clean(rec.note),
            )
        )  # fmt: skip

    keys = [
        tuple(
            k
            for k in (
                f"url:{_url_key(p.url)}" if p.url else "",
                f"name:{norm_name(p.name)}|{_norm_employer(p.employer)}"
                if norm_name(p.name)
                else "",
            )
            if k
        )
        for p in kept
    ]
    merged: list[_Person] = []
    for group in _dedupe_clusters(keys):
        head = kept[group[0]]
        for i in group[1:]:
            head.absorb(kept[i])
            stats["dropped_duplicates"] += 1
            exclusions.append(
                core.Exclusion("people", kept[i].key, f"duplicate of {head.key}", kept[i].name)
            )
        merged.append(head)

    built: list[tuple[int, int, str, dict[str, str]]] = []
    for p in merged:
        number, level_label = _level(p.title, p.level)
        # "Meets seniority rule" is the level alone. A person below the rule stays in the list
        # only when the title names a lane platform (and says No); otherwise the row is dropped.
        senior = rules.is_senior(number)
        if not senior and not _names_platform(profile, p.title):
            stats["dropped_below_rule"] = stats.get("dropped_below_rule", 0) + 1
            exclusions.append(
                core.Exclusion(
                    "people",
                    p.key,
                    "below the seniority rule and the title names no platform",
                    p.name,
                )
            )
            continue
        rule = _entity(profile, accounts, p)
        priority = _best_priority(p.priorities, p.title)
        lane_text = "; ".join(p.lanes) if p.lanes else rules.lane(p.title)
        linked = _linkedin(p.url)
        host = departed.get(norm_name(p.name), "")
        note = p.note
        if host and norm_name(p.name) not in current:
            flag = f"Departed per {host}"
            if flag not in note:
                note = f"{note}; {flag}" if note else flag
            stats["departed_flagged"] += 1
        row = {
            "Name": p.name,
            "Title as reported": p.title or core.NOT_FOUND,
            "Level": level_label,
            "Meets seniority rule": "Yes" if senior else "No",
            "Lane": lane_text or core.NOT_FOUND,
            "Priority (A/B/C)": priority,
            "Former SI and consulting employers": p.former_si or core.NOT_FOUND,
            "Found by": p.found_by or core.NOT_FOUND,
            "LinkedIn": linked or core.NOT_FOUND,
            "Location": p.location or core.NOT_FOUND,
            "Entity": rule.label if rule else profile.label,
            "Last verified": core.iso(p.last_verified),
            "Note": note or core.NOT_APPLICABLE,
        }
        if rule is not None and not rule.counts_as_account:
            stats["non_account_entity"] += 1
        built.append((_PRIORITY_ORDER[priority], number, p.name.casefold(), row))
    built.sort(key=lambda t: t[:3])
    return [t[3] for t in built]


def _people_stats(
    profile: core.WorkspaceProfile, rows: list[dict[str, str]], stats: dict[str, Any]
) -> None:
    """The counts over the account's rows (a divested business does not count)."""
    divested = {r.label for r in profile.entities if not r.counts_as_account}
    real = [r for r in rows if not r["Name"].startswith("Not collected")]
    counted = [r for r in real if r["Entity"] not in divested]
    stats["rows"] = len(real)
    stats["total"] = len(counted)
    stats["meets_seniority"] = sum(1 for r in counted if r["Meets seniority rule"] == "Yes")
    stats["priority_a_senior"] = sum(
        1 for r in counted if r["Priority (A/B/C)"] == "A" and r["Meets seniority rule"] == "Yes"
    )
    stats["per_level"] = {
        label: sum(1 for r in counted if r["Level"] == label) for label in rules.LEVELS
    }


# ---------------------------------------------------------------------------- Leadership rows
def _build_leadership_rows(
    profile: core.WorkspaceProfile,
    records: Sequence[LeadershipRecord],
    exclusions: list[core.Exclusion],
    stats: dict[str, Any],
) -> list[dict[str, str]]:
    stats.update(leadership_dropped=0, leadership_duplicates=0)
    if not records:
        return [_placeholder_row(core.LEADERSHIP_COLUMNS, NO_LEADERSHIP_REASON)]

    entries: list[dict[str, Any]] = []
    for rec in records:
        resolved = _resolve_name(rec.name)
        if resolved is None:
            stats["leadership_dropped"] += 1
            exclusions.append(
                core.Exclusion("leadership", rec.name, "not a valid person name", rec.role)
            )
            continue
        name, remainder = resolved
        url = _clean(rec.source_url)
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not host_of(url):
            stats["leadership_dropped"] += 1
            exclusions.append(core.Exclusion("leadership", name, "no source URL", _clean(rec.role)))
            continue
        entries.append(
            {
                "name": name, "role": _clean(rec.role) or remainder, "start": rec.start,
                "end": rec.end, "url": url, "type": _clean(rec.source_type),
                "status": _clean(rec.status), "note": _clean(rec.note),
            }
        )  # fmt: skip
    keys = [(f"{norm_name(e['name'])}|{fold(e['role'])}",) for e in entries]
    out: list[dict[str, str]] = []
    for group in _dedupe_clusters(keys):
        head = entries[group[0]]
        for i in group[1:]:
            for k, v in entries[i].items():
                if not head[k] and v:
                    head[k] = v
            stats["leadership_duplicates"] += 1
            exclusions.append(
                core.Exclusion(
                    "leadership", entries[i]["name"], "duplicate leadership row", head["role"]
                )
            )
        status = _CANONICAL_STATUS.get(head["status"].casefold(), "")
        end: date | None = head["end"]
        if not status:
            blank = not head["status"]
            status = "Departed" if blank and end and end <= profile.run_date else "Unverified"
        if end:
            end_cell = core.iso(end)
        else:
            end_cell = core.NOT_APPLICABLE if status == "Current" else core.UNKNOWN
        out.append(
            {
                "Name": head["name"],
                "Role": head["role"] or core.NOT_FOUND,
                "Start date": core.iso(head["start"]),
                "End or retire date": end_cell,
                "Status": status,
                "Source URL": head["url"],
                "Source type": head["type"] or core.UNKNOWN,
                "Note": head["note"] or core.NOT_APPLICABLE,
            }
        )
    if not out:
        return [_placeholder_row(core.LEADERSHIP_COLUMNS, "no leadership row passed the checks")]
    return out


def _leadership_stats(rows: list[dict[str, str]], stats: dict[str, Any]) -> None:
    real = [r for r in rows if not r["Name"].startswith("Not collected")]
    stats["leadership_total"] = len(real)
    for status in core.LEADERSHIP_STATUSES:
        key = "leadership_" + re.sub(r"\W+", "_", status.lower()).strip("_")
        stats[key] = sum(1 for r in real if r["Status"] == status)


def build_people(
    profile: core.WorkspaceProfile,
    people: Sequence[PersonRecord],
    leadership: Sequence[LeadershipRecord],
) -> core.RepairedTable:
    """The repaired People table (sheets ``People`` and ``Leadership``) for one workspace."""
    exclusions: list[core.Exclusion] = []
    notes: list[str] = []
    stats: dict[str, Any] = {}
    leader_rows = _build_leadership_rows(profile, leadership, exclusions, stats)
    _leadership_stats(leader_rows, stats)
    departed: dict[str, str] = {}
    current: set[str] = set()
    for row in leader_rows:
        key = norm_name(row["Name"])
        if row["Status"] == "Departed":
            departed.setdefault(key, host_of(row["Source URL"]))
        elif row["Status"] == "Current":
            current.add(key)
    rows = _build_people_rows(
        profile, people, departed, frozenset(current), exclusions, stats, notes
    )
    _people_stats(profile, rows, stats)
    if not rows:
        rows = [_placeholder_row(core.PEOPLE_COLUMNS, "no people row passed the checks")]
    return core.RepairedTable(
        key="people",
        title="People",
        sheets=[
            core.Sheet("People", core.PEOPLE_COLUMNS, rows),
            core.Sheet("Leadership", core.LEADERSHIP_COLUMNS, leader_rows),
        ],
        exclusions=exclusions,
        stats=stats,
        notes=notes,
    )
