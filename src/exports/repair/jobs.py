"""Job postings of the repaired export (spec table 1): one row per unique posting, platforms and
entity resolved, status and dates honest, every cell filled or explicitly marked.

Pipeline: records with no source URL are dropped (rule 5); the rest are keyed on
``keys.posting_id`` and merged (platforms and URLs union, earliest first seen, latest last seen,
the record with a description preferred). A title that is empty or equal to a dictionary platform
(a research fact, not a posting) is recovered from the URL slug or the posting is dropped. Platforms
and lanes come only from the dictionary (``config/parity/platforms.yaml``) found in the title and
description (``parity.rules.match_platforms``); platforms the profile marks out of scope are never
listed.

Conventions chosen here (the callers and the checks rely on them):

* **Outside window flag.** The event date of a posting is ``Posted`` if known, else ``First seen``.
  When it lies outside ``[profile.window_from, profile.window_to]`` the row is kept and the literal
  ``Outside window`` (``core.OUTSIDE_WINDOW``) is written into the ``Posted`` cell (the real first
  and last seen dates stay in their own cells; the real posted date is in the exclusion detail).
  ``ResolvedPosting.outside_window`` is True. Such a row is excluded from every count. An unknown
  event date is never flagged. ``Status`` always stays Open / Closed.
* **Counts** (``stats``) use only rows whose entity counts as the account and that are inside the
  window. Rows of other entities (a divested business, ``Not the account``) stay in the sheets with
  their ``Entity`` and are summarised in ``stats['non_account_entities']``.
* **Status.** ``open`` gives ``Open``; ``closed`` / ``expired`` / ``filled`` give ``Closed``; any
  other stored word (unknown, found, blank) gives ``Closed (not found on site, <run date>)``.
* **Class** is the stored class when present (build, admin_owner, working_tool, user), else
  ``parity.rules.classify`` over the platform sentences.

``build_jobs`` returns a ``RepairedTable`` whose ``stats['resolved']`` holds the list of
``ResolvedPosting`` for the tech, signals and check builders.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import unquote, urlsplit

from exports.repair.core import (
    JOBS_ALL_COLUMNS,
    JOBS_COLUMNS,
    NOT_APPLICABLE,
    NOT_FOUND,
    OUTSIDE_WINDOW,
    UNKNOWN,
    Exclusion,
    RepairedTable,
    Sheet,
    WorkspaceProfile,
    iso,
    not_collected,
    span,
)
from exports.repair.entity import tag_entity
from exports.repair.keys import canonical_url, fold, posting_id
from exports.repair.records import JobRecord
from parity.platforms import Platforms, default_platforms
from parity.rules import CLASS_KEYS, classify, join_sentences, match_platforms

MAX_DESCRIPTION = 32000
OUTSIDE_WINDOW_FLAG = OUTSIDE_WINDOW
_OPEN = frozenset({"open", "opened", "active", "live"})
_CLOSED = frozenset({"closed", "expired", "filled", "removed", "inactive"})
_STORED_CLASS = {
    "build": "Build role",
    "build role": "Build role",
    "admin": "Admin and owner role",
    "admin_owner": "Admin and owner role",
    "admin and owner role": "Admin and owner role",
    "owner": "Admin and owner role",
    "working_tool": "Working tool",
    "working tool": "Working tool",
    "tool": "Working tool",
    "user": "User role",
    "user role": "User role",
}
_GENERIC_SEGMENTS = frozenset(
    {
        "job", "jobs", "career", "careers", "search", "details", "detail", "position", "positions",
        "opening", "openings", "vacancy", "vacancies", "requisition", "posting", "postings",
        "apply", "view", "en", "us", "global", "external", "site", "sites", "portal", "listing",
        "listings", "opportunity", "opportunities", "o", "p", "jobdetail", "jobdetails",
    }
)  # fmt: skip
_LOCALE = re.compile(r"^[a-z]{2}([-_][a-z]{2})?$", re.I)
_EMBEDDED_ID = re.compile(r"[_-](?:R-?|JR-?|REQ-?)\d{3,}(?:-\d+)?$", re.I)
_DIGITS = re.compile(r"^\d{4,}$")
_LOWER_WORD = re.compile(r"^[a-z][a-z0-9]*$")


# ---------------------------------------------------------------- the resolved posting
@dataclass(frozen=True)
class ResolvedPosting:
    """One unique posting after merging, title repair, platform and entity resolution."""

    posting_id: str
    requisition_id: str
    title: str
    entity: str
    counts_as_account: bool
    platforms: tuple[str, ...]
    lanes: tuple[str, ...]
    posting_class: str
    status: str
    first_seen: date | None
    last_seen: date | None
    posted: date | None
    location: str
    seniority: str
    team: str
    function: str
    sentences: str
    description: str
    url: str
    collected: date | None
    outside_window: bool
    source_kind: str
    urls: tuple[str, ...] = ()

    @property
    def is_open(self) -> bool:
        """Whether the posting is Open."""
        return self.status == "Open"

    @property
    def counted(self) -> bool:
        """Whether the posting counts: the account's own and inside the window."""
        return self.counts_as_account and not self.outside_window


# ---------------------------------------------------------------- dictionary helpers
def get_platforms(profile: WorkspaceProfile) -> Platforms:
    """The profile's platform dictionary, or the repository one."""
    return profile.platforms if profile.platforms is not None else default_platforms()


def out_of_scope_names(profile: WorkspaceProfile) -> frozenset[str]:
    """Folded names of the platforms never listed for this workspace (profile plus dictionary)."""
    names = [*profile.out_of_scope_platforms, *get_platforms(profile).out_of_scope]
    return frozenset(fold(n) for n in names if n)


def _skip_names(profile: WorkspaceProfile, platforms: Platforms) -> frozenset[str]:
    oos = out_of_scope_names(profile)
    return frozenset(lane.name for lane in platforms.lanes if fold(lane.name) in oos)


def _dictionary_names(platforms: Platforms) -> frozenset[str]:
    names = {fold(lane.name) for lane in platforms.lanes}
    names |= {fold(entry.name) for entry in platforms.ranking}
    return frozenset(names)


def _is_platform_title(title: str, platforms: Platforms, extra: frozenset[str]) -> bool:
    text = title.strip()
    if fold(text) in _dictionary_names(platforms) or fold(text) in extra:
        return True
    for lane in platforms.lanes:
        if re.fullmatch(lane.pattern, text, lane.flags):
            return True
    return any(entry.regex.fullmatch(text) for entry in platforms.ranking)


def _lane_of(platforms: Platforms) -> dict[str, str]:
    """Platform name -> the lane label it files under (the ranking lane, else its own name)."""
    ranking = {entry.name: entry.lane for entry in platforms.ranking}
    return {lane.name: ranking.get(lane.name) or lane.name for lane in platforms.lanes}


# ---------------------------------------------------------------- title from the URL
def _humanise(slug: str) -> str:
    text = re.sub(r"[-_+]+", " ", unquote(slug))
    text = re.sub(r"\s+", " ", text).strip()
    words = [w[0].upper() + w[1:] if _LOWER_WORD.match(w) else w for w in text.split()]
    return " ".join(words)


def _usable_slug(seg: str) -> bool:
    low = seg.lower()
    return not (
        not seg
        or _DIGITS.match(seg)
        or low in _GENERIC_SEGMENTS
        or _LOCALE.match(seg)
        or sum(c.isalpha() for c in seg) < 4
    )


def title_from_url(url: str) -> str:
    """A job title read from a careers URL slug (the path segment before the id), humanised, or
    ``""`` when the URL has no usable slug."""
    segments = [s for s in urlsplit(url or "").path.split("/") if s]
    if not segments:
        return ""
    last = segments[-1]
    if _EMBEDDED_ID.search(last):
        slug = _EMBEDDED_ID.sub("", last)
        return _humanise(slug) if _usable_slug(slug) else ""
    idx = max((i for i, s in enumerate(segments) if _DIGITS.match(s)), default=-1)
    if idx < 0:
        return ""
    for j in range(idx - 1, -1, -1):
        if _usable_slug(segments[j]):
            return _humanise(segments[j])
    for j in range(idx + 1, len(segments)):
        if _usable_slug(segments[j]):
            return _humanise(segments[j])
    return ""


# ---------------------------------------------------------------- field resolution
def _status_word(word: str) -> str:
    low = (word or "").strip().lower()
    if low in _OPEN:
        return "open"
    if low in _CLOSED:
        return "closed"
    return "unknown"


def _status_cell(profile: WorkspaceProfile, rec: JobRecord) -> str:
    word = _status_word(rec.status)
    if word == "open":
        return "Open"
    if word == "closed":
        return "Closed"
    return f"Closed (not found on site, {profile.run_date.isoformat()})"


def _pick_status(profile: WorkspaceProfile, recs: list[JobRecord]) -> str:
    """The status of the record that was checked or seen last; Open wins a tie."""
    best = max(
        recs,
        key=lambda r: (
            r.status_checked or date.min,
            r.last_seen or date.min,
            _status_word(r.status) == "open",
        ),
    )
    return _status_cell(profile, best)


def _stored_class(recs: list[JobRecord]) -> str:
    for rec in recs:
        mapped = _STORED_CLASS.get((rec.stored_class or "").strip().lower())
        if mapped:
            return mapped
    return ""


def _record_pid(rec: JobRecord, url: str) -> str:
    pid = posting_id(url, rec.requisition_id)
    if pid == canonical_url(url):
        for other in rec.other_urls:
            alt = posting_id(other, rec.requisition_id)
            if alt != canonical_url(other):
                return alt
    return pid


def _preferred(recs: list[tuple[JobRecord, str]]) -> tuple[JobRecord, str]:
    return max(
        recs,
        key=lambda ru: (
            bool(ru[0].description.strip()),
            len(ru[0].description),
            ru[0].source_kind == "live_board",
            bool(ru[0].title.strip()),
            ru[1],
        ),
    )


def _earliest(values: list[date | None]) -> date | None:
    real = [v for v in values if v]
    return min(real) if real else None


def _latest(values: list[date | None]) -> date | None:
    real = [v for v in values if v]
    return max(real) if real else None


def cut_at_word(text: str, limit: int = MAX_DESCRIPTION) -> str:
    """``text`` cut to at most ``limit`` characters at a word boundary."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut).rstrip()


def _title_for(
    ordered: list[tuple[JobRecord, str]],
    platforms: Platforms,
    extra: frozenset[str],
) -> tuple[str, str]:
    """``(title, "")`` or ``("", reason)``."""
    saw_platform = False
    for rec, _url in ordered:
        title = re.sub(r"\s+", " ", rec.title or "").strip()
        if not title:
            continue
        if _is_platform_title(title, platforms, extra):
            saw_platform = True
            continue
        return title, ""
    for rec, url in ordered:
        for candidate in (url, *rec.other_urls):
            slug = title_from_url(candidate)
            if slug and not _is_platform_title(slug, platforms, extra):
                return slug, ""
    if saw_platform:
        return "", "title is a platform name"
    return "", "title is empty and not recoverable from the URL"


# ---------------------------------------------------------------- the resolver
def resolve_jobs(profile: WorkspaceProfile, records: list[JobRecord]) -> list[ResolvedPosting]:
    """The unique postings of ``records`` (see the module docstring). Dropped rows are reported
    by ``build_jobs``; this returns only the kept postings, in a stable order."""
    return _resolve(profile, records)[0]


def _resolve(
    profile: WorkspaceProfile, records: list[JobRecord]
) -> tuple[list[ResolvedPosting], list[Exclusion], int]:
    platforms = get_platforms(profile)
    skip = _skip_names(profile, platforms)
    lane_of = _lane_of(platforms)
    lane_by_fold = {fold(n): n for n in lane_of}
    extra = out_of_scope_names(profile)
    exclusions: list[Exclusion] = []
    groups: dict[str, list[tuple[JobRecord, str]]] = defaultdict(list)
    for rec in records:
        url = (rec.url or "").strip() or next((u.strip() for u in rec.other_urls if u.strip()), "")
        if not url:
            exclusions.append(Exclusion("jobs", rec.key, "no source URL", rec.title[:80]))
            continue
        groups[_record_pid(rec, url)].append((rec, url))
    out: list[ResolvedPosting] = []
    merged = 0
    for pid in sorted(groups):
        group = groups[pid]
        pref, url = _preferred(group)
        ordered = [(pref, url), *sorted((g for g in group if g[0] is not pref), key=lambda g: g[1])]
        title, reason = _title_for(ordered, platforms, extra)
        if not title:
            exclusions.append(Exclusion("jobs", pid, reason, (pref.title or url)[:120]))
            continue
        recs = [g[0] for g in group]
        merged += len(group) - 1
        description = pref.description.strip() or max(
            (r.description.strip() for r in recs), key=len, default=""
        )
        found = match_platforms(title, description, platforms, skip)
        names = list(found)
        for rec in recs:
            for stored in rec.stored_platforms:
                canon = lane_by_fold.get(fold(stored))
                if canon and canon not in skip and canon not in names:
                    names.append(canon)
        order = {lane.name: i for i, lane in enumerate(platforms.lanes)}
        names.sort(key=lambda n: order.get(n, len(order)))
        lanes: list[str] = []
        for name in names:
            lane = lane_of.get(name, name)
            if lane not in lanes:
                lanes.append(lane)
        stored = _stored_class(recs)
        decision = (stored, "") if stored else None
        cls = classify(decision, found)[0] if names else NOT_APPLICABLE
        first_seen = _earliest([r.first_seen for r in recs])
        posted = _earliest([r.posted_at for r in recs])
        requisition = next((r.requisition_id.strip() for r in recs if r.requisition_id.strip()), "")
        entity, counts = tag_entity(
            profile, url=url, title=title, text=description, first_seen=first_seen
        )
        event = posted or first_seen
        outside = event is not None and not span(profile.window_from, profile.window_to, event)
        urls: list[str] = []
        for rec, rec_url in group:
            for u in (rec_url, *rec.other_urls):
                if u.strip() and u.strip() not in urls:
                    urls.append(u.strip())
        kind = (
            "live_board" if any(r.source_kind == "live_board" for r in recs) else pref.source_kind
        )
        out.append(
            ResolvedPosting(
                posting_id=pid,
                requisition_id=requisition,
                title=title,
                entity=entity,
                counts_as_account=counts,
                platforms=tuple(names),
                lanes=tuple(lanes),
                posting_class=cls,
                status=_pick_status(profile, recs),
                first_seen=first_seen,
                last_seen=_latest([r.last_seen for r in recs]),
                posted=posted,
                location=next(
                    (r.location.strip() for r in [pref, *recs] if r.location.strip()), ""
                ),
                seniority=next(
                    (r.seniority.strip() for r in [pref, *recs] if r.seniority.strip()), ""
                ),
                team=next((r.team.strip() for r in [pref, *recs] if r.team.strip()), ""),
                function=next(
                    (r.function.strip() for r in [pref, *recs] if r.function.strip()), ""
                ),
                sentences=join_sentences({n: found[n] for n in names if n in found}),
                description=description,
                url=url,
                collected=_latest([r.collected for r in recs]),
                outside_window=outside,
                source_kind=kind,
                urls=tuple(urls),
            )
        )
    out.sort(
        key=lambda p: (
            -(p.first_seen.toordinal() if p.first_seen else 0),
            fold(p.title),
            p.posting_id,
        )
    )
    return out, exclusions, merged


# ---------------------------------------------------------------- the table
def _cell(value: str) -> str:
    return value.strip() or NOT_FOUND


def _status_kind(status: str) -> str:
    if status == "Open":
        return "Open"
    return "Closed (not found on site)" if status.startswith("Closed (") else "Closed"


def _job_row(p: ResolvedPosting) -> dict[str, str]:
    return {
        "Requisition ID": _cell(p.requisition_id),
        "Posting ID": p.posting_id,
        "Title": p.title,
        "Entity": p.entity,
        "Platforms named": ", ".join(p.platforms) or NOT_APPLICABLE,
        "Lane(s)": ", ".join(p.lanes) or NOT_APPLICABLE,
        "Class": p.posting_class,
        "Status": p.status,
        "First seen": iso(p.first_seen),
        "Last seen": iso(p.last_seen),
        "Posted": OUTSIDE_WINDOW_FLAG if p.outside_window else iso(p.posted),
        "Location": _cell(p.location),
        "Seniority": _cell(p.seniority),
        "Platform sentences": (p.sentences or NOT_FOUND) if p.platforms else NOT_APPLICABLE,
        "Full description": cut_at_word(p.description)
        if p.description
        else not_collected("description not collected"),
        "Source URL": p.url,
        "Collected": iso(p.collected)
        if p.collected
        else not_collected("collection date not recorded"),
    }


def build_jobs(profile: WorkspaceProfile, records: list[JobRecord]) -> RepairedTable:
    """The Job postings table: the platform postings sheet, the unfiltered sheet, the exclusion
    log and the statistics (see the module docstring)."""
    platforms = get_platforms(profile)
    skip = _skip_names(profile, platforms)
    postings, exclusions, merged = _resolve(profile, records)
    dropped = len(exclusions)
    main = Sheet("Job postings", JOBS_COLUMNS)
    everything = Sheet("All postings (unfiltered)", JOBS_ALL_COLUMNS)
    status_resolved: Counter[str] = Counter()
    other: dict[str, dict[str, int]] = {}
    per_platform: dict[str, dict[str, int]] = {
        lane.name: {"postings": 0, "open": 0, "build": 0, "admin": 0, "tool": 0, "user": 0}
        for lane in platforms.lanes
        if lane.name not in skip
    }
    classes = {label: key for label, key in CLASS_KEYS.items()}
    platform_postings = account_platform = outside = 0
    for p in postings:
        row = _job_row(p)
        everything.rows.append({c: row[c] for c in JOBS_ALL_COLUMNS})
        if p.platforms:
            main.rows.append(row)
            platform_postings += 1
        status_resolved[_status_kind(p.status)] += 1
        why = []
        if not p.counts_as_account:
            why.append(f"entity {p.entity}")
            slot = other.setdefault(p.entity, {"postings": 0, "platform_postings": 0})
            slot["postings"] += 1
            slot["platform_postings"] += 1 if p.platforms else 0
        if p.outside_window:
            why.append("outside the window")
            outside += 1
        if why:
            detail = f"posted {iso(p.posted)}, first seen {iso(p.first_seen)}"
            exclusions.append(
                Exclusion("jobs", p.posting_id, "excluded from counts: " + ", ".join(why), detail)
            )
            continue
        if not p.platforms:
            continue
        account_platform += 1
        for name in p.platforms:
            found = per_platform.get(name)
            if found is None:
                continue
            slot = found
            slot["postings"] += 1
            slot["open"] += 1 if p.is_open else 0
            key = classes.get(p.posting_class)
            if key:
                slot[key] += 1
    stats: dict[str, Any] = {
        "resolved": postings,
        "rows_in": len(records),
        "unique_postings": len(postings),
        "platform_postings": platform_postings,
        "account_platform_postings": account_platform,
        "per_platform": per_platform,
        "non_account_entities": other,
        "outside_window": outside,
        "duplicates_removed": merged,
        "dropped": dropped,
        "status_resolved": dict(status_resolved),
    }
    notes = []
    if outside:
        notes.append(
            f"{outside} postings fall outside the window "
            f"{profile.window_from.isoformat()} to {profile.window_to.isoformat()}: kept, flagged "
            f"'{OUTSIDE_WINDOW}' in Posted, and left out of every count."
        )
    if other:
        notes.append(
            "Postings of other entities are listed with their Entity and left out of every "
            "count: " + ", ".join(f"{k} ({v['postings']})" for k, v in sorted(other.items()))
        )
    return RepairedTable(
        key="jobs",
        title="Job postings",
        sheets=[main, everything],
        exclusions=exclusions,
        stats=stats,
        notes=notes,
    )


__all__ = [
    "MAX_DESCRIPTION",
    "OUTSIDE_WINDOW_FLAG",
    "UNKNOWN",
    "ResolvedPosting",
    "build_jobs",
    "cut_at_word",
    "get_platforms",
    "out_of_scope_names",
    "resolve_jobs",
    "title_from_url",
]
