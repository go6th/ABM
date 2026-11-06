"""Workspace import, phase 2 (2026-11-03): the deliverable workbooks of an uploaded workspace
zip into the native tables, through the modules that own them.

``documents.deliverables`` recognises the Job Postings, People List and Tech Stack and Teams
sheets by their headers and, for a loose workbook upload, records each row as a private fact.
For a workspace package (``documents.workspace``: a zip with a root marker) the owner's rule is
stronger: every row is stored in its proper table, once, so the deep dive, the counts and a
rebuilt file set read it like a research's own rows. This module does that:

* postings go to ``job_postings`` with their platform sentences and classification
  (``jobs.repo.import_postings``), the posting text to the payload store so a rebuilt workbook
  prints it, the entity tag, the "what the posting says" summary and every location kept;
* people go to ``people`` with a ``person_sources`` row and, when the workbook names former
  employers, a ``person_profiles`` row (``people.repo.import_people``), the note and a second
  profile kept;
* the Confirmed stack goes to ``tech_stack_items``, the Platform Ranking to
  ``tech_detections`` (with open now, in confirmed stack, checked by reading), the Platform
  teams to ``platform_teams`` (``agents.production.pipelines.import_*``);

all under the one file-import run of the document (``runs.lifecycle.record_file_import_run``),
idempotent per row. Duplicates are merged before writing (owner's rule, 2026-11-03): a posting
by requisition, then posting URL, then title + location + entity (one requisition in several
places keeps all its locations); a person by profile link, then name + title; a stack row by
function + category + technology; a platform by name; a team by name. The survivor keeps the
earliest first seen, the latest last seen, Open when any copy is open, the union of platforms
and sentences, the fullest text, and ``merged_count``; the member's row on the Uploaded files
sheet shows file rows = imported + merged + skipped.

No model is called and nothing leaves the system.

Natural keys (PR3, 2026-11-04). Every row hangs off the document's one file-import run
(``file-import-<document id>``, reused on every import of the document), so a key is (tenant,
account, document, natural key). Each writer inserts a row only when the run holds none with its
key: importing the same upload again writes nothing new and gives identical row counts
(``tests/documents/test_workspace_reimport_db.py``). The rows are never rewritten in place: the
runtime role may only insert into these research tables ("postings are observations", 0010), and
an erased person is never brought back. A corrected file is a new upload, whose own run the
account page reads as the newest (``replica.current``).

=====================  =========================================================================
table                  natural key (besides tenant and run)
=====================  =========================================================================
job_postings           posting_key: requisition, else posting URL, else title + location + entity
job_posting_*          (posting_key, pass 0): written with their posting only
people                 person_key (from the source record: profile link, else name + title)
person_sources         source_record_id (a leadership-page entry folded into a held person too)
person_profiles        profile_url_canonical
tech_stack_items       (business_function, category, technology)
tech_detections        technology
platform_teams         lower(team)
news_events            event_key: the feed's event id, else headline + date
org_connections        (company_domain, category)
sources                the source register's URL per account (``db.source_ids.import_source``)
facts                  write_key: the grading write path's content key (``record_imported_fact``)
signal_events          fingerprint of (account, attribute key, words) (``signals.events``)
report_sections        (version, kind, position)
coverage_rows          row_key of the run's seeded tracker (updated in place, the tracker's rule)
research_notes         (kind, title) (``repo.import_notes``)
pull_log_calls         the call's position in the pull log (``repo.import_pull_calls``)
=====================  =========================================================================

Retry (PR3): each part runs in its own transaction with its status in ``import_run_parts``; a
failed part is tried again by the sweep at most three times with a growing wait, and only the
failed part (``documents.import_status``, ``documents.deliverables.retry_import_parts``).

GUARANTEE(test_a_workspace_zip_imports_each_deliverable_row_once_and_skips_build_and_owner_files)
GUARANTEE(test_workspace_rows_map_to_native_records_and_merge_duplicates)
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import io
import posixpath
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text

from agents.production.pipelines import (
    ImportedDetection,
    ImportedStackItem,
    ImportedTeam,
    import_detections,
    import_platform_teams,
    import_stack_items,
)
from documents import import_status, repo
from documents.deliverables import PAGE, Found, SheetReader
from documents.shapes import member_of as member_of_location
from documents.workspace import MemberRole, may_import
from jobs.models import Classification as PostingClassification
from jobs.models import PlatformSentence, Posting
from jobs.normalise import canonical_url
from jobs.repo import ImportedPosting, import_postings
from jobs.window import classify_window
from obs.logging import log_context, logger
from people.models import Classification as PersonClassification
from people.models import Person
from people.repo import ImportedPerson, import_people

_log = logger(__name__)

PROVIDER_CODE = "file_import"
DOCX_ROLES = frozenset({MemberRole.DELIVERABLE_SUMMARY, MemberRole.DELIVERABLE_EVIDENCE})


def _own_package(roles: Mapping[str, MemberRole]) -> bool:
    """Whether the zip is the product's own package (a ``MANIFEST.json`` root marker): its
    grades were the platform's own, so the imported findings may be stated."""
    return any(
        role is MemberRole.RESTORE and path.rsplit("/", 1)[-1] == "MANIFEST.json"
        for path, role in roles.items()
    )


_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_YEAR_SPAN = re.compile(r"^(.*?)\s*\((\d{4})\s+to\s+(\d{4})\)\s*$")
_COUNTED = re.compile(r"^(.*?)\s*\((\d[\d,]*)\)\s*$")
_CLASS = {
    "build role": "build",
    "build": "build",
    "admin and owner role": "admin_owner",
    "admin or owner role": "admin_owner",
    "platform owner": "admin_owner",
    "admin_owner": "admin_owner",
    "working tool": "working_tool",
    "user role": "user",
    "user": "user",
}
_LEVELS = {
    "c-level, evp and svp": "c_level",
    "c-level": "c_level",
    "c level": "c_level",
    "evp": "evp",
    "svp": "svp",
    "vp": "vp",
    "senior director": "senior_director",
    "director": "director",
    "associate director": "associate_director",
    "head": "head",
    "senior manager": "senior_manager",
    "manager": "manager",
    "c-level and svp": "c_level",
    "c level and svp": "c_level",
    "lead, owner, specialist or developer": "other",
    "other": "other",
}
_SKIP_SENTENCE = re.compile(r"^\(?(named in the title only|title only)\)?$", re.IGNORECASE)


# --- postings -------------------------------------------------------------------------------


@functools.cache
def lane_of_platform() -> dict[str, str]:
    """Platform name (case-folded) to lane id, from ``config/job_lanes.yaml``."""
    from jobs.config import load_jobs_config

    out: dict[str, str] = {}
    for lane in load_jobs_config().lanes:
        for name, aliases in lane.platforms.items():
            out.setdefault(name.casefold(), lane.id)
            for alias in aliases:
                out.setdefault(alias.casefold(), lane.id)
    return out


def _date(value: str | None) -> date | None:
    m = _DATE.match(value or "")
    return date.fromisoformat(m.group(1)) if m else None


def _split(value: str | None, sep: str = ",") -> tuple[str, ...]:
    return tuple(x.strip() for x in (value or "").split(sep) if x.strip())


def _sentences(
    spec: str | None, description: str, platforms: Sequence[str] = ()
) -> tuple[PlatformSentence, ...]:
    """The workbook's "Platform sentences" as verbatim spans: ``Platform: s1 | s2 || Other: s3``
    (the reference workbook), or plain sentences separated by ``|`` which go to the platform
    they name, else the row's first platform. A span is located in the description when the
    sentence is found there, else it spans itself."""
    out: list[PlatformSentence] = []
    seen: set[str] = set()
    for block in (spec or "").split("||"):
        block = block.strip()
        if not block:
            continue
        head, _, rest = block.partition(":")
        named = head.strip()
        if _ and named and len(named) <= 40 and " " not in named.strip("()"):
            platform_of_block: str | None = named
            body = rest
        else:
            platform_of_block = None
            body = block
        for sentence in (x.strip() for x in body.split(" | ")):
            if not sentence or _SKIP_SENTENCE.match(sentence):
                continue
            digest = hashlib.sha256(sentence.encode("utf-8")).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            platform = platform_of_block or next(
                (p for p in platforms if p.casefold() in sentence.casefold()),
                platforms[0] if platforms else "platform",
            )
            start = description.find(sentence) if description else -1
            if start < 0:
                start = 0
            out.append(
                PlatformSentence(
                    platform=platform[:100],
                    text=sentence[:2000],
                    start=start,
                    end=start + len(sentence),
                    sha256=digest,
                )
            )
    return tuple(out)


def posting_of(found: Found, *, document_id: UUID, now: datetime) -> ImportedPosting:
    """One Jobs sheet row as the posting the native tables keep (text ref set by the caller)."""
    f = found.fields
    title = found.value
    url = f.get("url") or ""
    requisition = (f.get("requisition") or "").strip()
    location = f.get("location") or None
    entity = f.get("entity") or None
    weak = False
    if requisition:
        key = f"id:{requisition}"
    elif url and canonical_url(url):
        key = f"url:{canonical_url(url)}"
    else:
        weak = True
        digest = hashlib.sha256(
            f"{title}|{location or ''}|{entity or ''}".casefold().encode("utf-8")
        ).hexdigest()
        key = f"weak:{digest}"
    platforms = _split(f.get("lanes"))
    lanes = tuple(dict.fromkeys(lane_of_platform().get(p.casefold(), "other") for p in platforms))
    # the owner's sheet calls the careers site's posting number "Requisition"; the repair
    # stage prints it as the Requisition ID (it was "Not found" on every row, 2026-11-05)
    requisition_id = requisition or None
    first_seen = _date(f.get("posted"))
    last_seen = _date(f.get("last_seen"))
    status_word = (f.get("status") or "").strip().casefold()
    status = "open" if status_word == "open" else "closed" if status_word == "closed" else "unknown"
    description = f.get("description") or ""
    summary = f.get("summary") or None
    label = _CLASS.get((f.get("class") or "").strip().casefold())
    classification = (
        PostingClassification(
            label=label,
            classified_by="rule",
            reason="class as written in the uploaded workbook",
            rule_id="workspace_import",
            evidence_sentence=(summary or None),
        )
        if label
        else None
    )
    posting = Posting(
        posting_key=key,
        weak_key=weak,
        title=title[:500],
        location=location,
        url=url,
        urls=(url,) if url else (),
        provider_code=PROVIDER_CODE,
        raw_ref=f"document:{document_id}#passage:{found.seq}",
        retrieved_at=now.isoformat(),
        posted_at=first_seen,
        first_seen_at=first_seen,
        status=status,
        status_source="live_board" if "live board" in (location or "").casefold() else "feed",
        text_sha256=hashlib.sha256(description.encode("utf-8")).hexdigest()
        if description
        else None,
        text_complete=bool(description) or None,
        read=bool(summary),
        full_text_read=bool(summary),
        window_bucket=classify_window(first_seen, now.date()),
        platforms=platforms,
        lanes=lanes,
        sentences=_sentences(f.get("sentences"), description, platforms),
        classification=classification,
        last_seen=last_seen,
        seniority=(f.get("seniority") or None),
        captured_on=first_seen,
        requisition_id=requisition_id,
    )
    return ImportedPosting(
        posting=posting,
        entity_tag=entity,
        summary_read=summary,
        locations=(location,) if location else (),
    )


_ID_SEGMENT = re.compile(r"^\d{3,}$|^(?:R|JR|REQ)-?\d{3,}(?:-\d+)?$", re.I)


def careers_url_template(rows: Sequence[Found]) -> str | None:
    """The careers site's posting address pattern, learnt from the rows that carry a link whose
    path holds the row's requisition as one segment: ``https://careers.example.com/en/job/{id}/
    {slug}``. The commonest pattern wins; None when no linked row shows one."""
    seen: Counter[str] = Counter()
    for x in rows:
        url = str(x.fields.get("url") or "")
        req = str(x.fields.get("requisition") or "").strip()
        if not url.startswith("http") or not req:
            continue
        parts = urlsplit(url)
        segments = parts.path.split("/")
        hits = [i for i, seg in enumerate(segments) if seg == req]
        if not hits:
            continue
        i = hits[-1]
        head = "/".join(segments[:i])
        tail = segments[i + 1 :]
        pattern = f"{parts.scheme}://{parts.netloc}{head}/{{id}}" + ("/{slug}" if tail else "")
        seen[pattern] += 1
    return seen.most_common(1)[0][0] if seen else None


def _slug(title: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")
    return text[:120] or "posting"


def _feed_key(title: str, location: str) -> tuple[str, str]:
    return (" ".join(title.casefold().split()), " ".join((location or "").casefold().split()))


def fill_posting_links(
    rows: Sequence[Found], template: str | None, feed: Sequence[Mapping[str, Any]]
) -> tuple[int, int]:
    """Give the linkless rows a web address: from the careers pattern when the row has a
    requisition, else from the feed record with the same title, location and first-seen day
    (then title and day). Returns ``(from the pattern, from the feed)``; a row with neither
    stays linkless."""
    from_pattern = from_feed = 0
    by_key: dict[tuple[str, str, str], str] = {}
    by_title: dict[tuple[str, str], str] = {}
    for rec in feed:
        url = str(rec.get("url") or "")
        if not url.startswith("http"):
            continue
        day = str(rec.get("first_seen") or "")[:10]
        title, location = _feed_key(str(rec.get("title") or ""), str(rec.get("location") or ""))
        by_key.setdefault((title, location, day), url)
        by_title.setdefault((title, day), url)
    for x in rows:
        req = str(x.fields.get("requisition") or "").strip()
        if template and req and _ID_SEGMENT.match(req):
            x.fields["url"] = template.format(id=req, slug=_slug(x.value))
            from_pattern += 1
            continue
        day = str(x.fields.get("posted") or "")[:10]
        title, location = _feed_key(x.value, str(x.fields.get("location") or ""))
        found = by_key.get((title, location, day)) or by_title.get((title, day))
        if found:
            x.fields["url"] = found
            from_feed += 1
    return from_pattern, from_feed


def merge_postings(
    items: Sequence[ImportedPosting], members: Sequence[str] = ()
) -> tuple[list[ImportedPosting], Counter[str]]:
    """Fold postings with one key into one row (see the module docstring); returns the
    survivors in sheet order and how many rows were folded, per member (``members`` is the
    member each item came from, in order; ``""`` when unknown)."""
    by_key: dict[str, ImportedPosting] = {}
    merged: Counter[str] = Counter()
    for i, item in enumerate(items):
        key = item.posting.posting_key
        have = by_key.get(key)
        if have is None:
            by_key[key] = item
            continue
        merged[members[i] if i < len(members) else ""] += 1
        a, b = have.posting, item.posting
        firsts = [d for d in (a.first_seen_at, b.first_seen_at) if d]
        lasts = [d for d in (a.last_seen, b.last_seen) if d]
        sentences = {s.sha256: s for s in (*a.sentences, *b.sentences)}
        status = "open" if "open" in (a.status, b.status) else a.status
        longer = a if (a.text_sha256 and not b.text_sha256) or len(a.title) >= 0 else b
        survivor = a.model_copy(
            update={
                "first_seen_at": min(firsts) if firsts else None,
                "posted_at": min(firsts) if firsts else None,
                "captured_on": min(firsts) if firsts else None,
                "last_seen": max(lasts) if lasts else None,
                "status": status,
                "platforms": tuple(dict.fromkeys((*a.platforms, *b.platforms))),
                "lanes": tuple(dict.fromkeys((*a.lanes, *b.lanes))),
                "sentences": tuple(sentences.values()),
                "urls": tuple(dict.fromkeys((*a.urls, *b.urls))),
                "text_sha256": longer.text_sha256,
                "text_complete": longer.text_complete,
                "read": a.read or b.read,
                "full_text_read": a.full_text_read or b.full_text_read,
                "classification": a.classification or b.classification,
            }
        )
        by_key[key] = ImportedPosting(
            posting=survivor,
            entity_tag=have.entity_tag or item.entity_tag,
            summary_read=have.summary_read or item.summary_read,
            locations=tuple(dict.fromkeys((*have.locations, *item.locations))),
            merged_count=have.merged_count + 1,
        )
    return list(by_key.values()), merged


def keep_rows(
    items: Sequence[ImportedPosting], rows: Sequence[Found]
) -> tuple[list[ImportedPosting], Counter[str]]:
    """Every sheet row as its own posting row (the owner's rule, 2026-11-05: an upload comes
    back word for word, nothing folded). The table keys a run's postings uniquely, so a
    requisition the sheet lists more than once (the same role in several cities, a careers and
    a LinkedIn copy, a role read in full on the hidden-title sheet) keeps its key on the first
    row and ``<key>#<sheet row>`` on the others. Returns the rows and an empty fold count."""
    seen: set[str] = set()
    out: list[ImportedPosting] = []
    for item, row in zip(items, rows, strict=True):
        key = item.posting.posting_key
        if key in seen:
            where = row.location.get("row")
            suffix = str(where) if isinstance(where, int | float) else str(row.seq)
            key = f"{key}#{suffix}"
            item = ImportedPosting(
                posting=item.posting.model_copy(update={"posting_key": key}),
                entity_tag=item.entity_tag,
                summary_read=item.summary_read,
                locations=item.locations,
                merged_count=0,
            )
        seen.add(key)
        out.append(item)
    return out, Counter()


# --- people ---------------------------------------------------------------------------------


@functools.cache
def _people_config() -> Any:
    from people.config import load_people_config

    return load_people_config()


def _profile_url(link: str | None) -> str | None:
    if not link:
        return None
    from people.urls import canonical_profile_url

    try:
        return canonical_profile_url(link, _people_config().profile_url) or link[:2000]
    except Exception:  # noqa: BLE001 - an odd link is kept as written
        return link[:2000]


def _level(label: str | None, title: str | None) -> str:
    # the owner's People List numbers its levels ("1 C-level and SVP", "9 Lead, owner,
    # specialist or developer"): the number is order, not part of the label (UCB, 2026-11-05)
    clean = re.sub(r"^\d+\s+", "", (label or "").strip()).casefold()
    code = _LEVELS.get(clean)
    if code:
        return code
    from people.level import classify_level

    cfg = _people_config()
    match = classify_level(title, cfg.level_rules, cfg.level_order)
    return match.level if match is not None else "other"


def former_employers(spec: str | None) -> tuple[tuple[str, int | None, int | None], ...]:
    """``"Firm (2017 to 2019); Other (2020 to 2021)"`` as (employer, start, end) tuples."""
    out: list[tuple[str, int | None, int | None]] = []
    for part in (spec or "").split(";"):
        part = part.strip()
        if not part:
            continue
        m = _YEAR_SPAN.match(part)
        if m:
            out.append((m.group(1).strip()[:200], int(m.group(2)), int(m.group(3))))
        else:
            out.append((part[:200], None, None))
    return tuple(out)


def person_of(
    found: Found, *, document_id: UUID, notes: Mapping[str, str] | None = None
) -> ImportedPerson:
    """One People sheet row as the person the native tables keep; ``notes`` are the Account
    Summary's key-people notes by name (case-folded), kept on the row."""
    f = found.fields
    name = found.value[:200]
    title = f.get("title") or None
    profile_url = _profile_url(f.get("link"))
    found_by = (f.get("found_by") or "").casefold()
    origin = (
        "association"
        if "association" in found_by or "gap check" in found_by
        else "title_search"
        if "title search" in found_by or not found_by
        else "mention"
    )
    identity = profile_url or f"{name}|{title or ''}"
    source_record_id = f"import:{identity}"[:300]
    from people.pipeline import person_key

    level = _level(f.get("level"), title)
    priority = (f.get("priority") or "").strip().upper()[:1]
    priority = priority if priority in ("A", "B", "C") else ""
    lane = (f.get("lane") or "").strip() or None
    # the workbook's own seniority verdict travels with the row ("meets seniority rule" Yes,
    # No, or "No (not current ...)"): the repair stage prints it, it never recomputes it
    meets = (f.get("meets_seniority") or "").strip()
    evidence = f"Level {f.get('level') or level}; priority {priority or 'none'}"
    if meets:
        evidence += f"; meets seniority rule {meets}"
    if profile_url and priority:
        classification = PersonClassification(
            status="in_scope",
            rule_id="workspace_import",
            evidence=evidence,
            level=level,
            lanes=(lane,) if lane else (),
            role_lane=lane,
            priority=priority,
        )
    else:
        classification = PersonClassification(
            status="review",
            reason="no profile link in the workbook"
            if not profile_url
            else "no priority in the workbook",
            rule_id="workspace_import",
            evidence=evidence,
            level=level,
            lanes=(lane,) if lane else (),
            role_lane=lane,
        )
    person = Person(
        person_key=person_key(source_record_id),
        source_record_id=source_record_id,
        origin=origin,
        name=name,
        title=title[:300] if title else None,
        profile_url=profile_url,
        profile_url_raw=f.get("link"),
        location=(f.get("location") or None),
        provider_code=PROVIDER_CODE,
        raw_ref=f"document:{document_id}#passage:{found.seq}",
        classification=classification,
    )
    return ImportedPerson(
        person=person,
        note=(f.get("note") or (notes or {}).get(name.casefold()) or None),
        second_profile_url=_profile_url(f.get("second_link")),
        former_employers=former_employers(f.get("former_si")),
    )


def merge_people(
    items: Sequence[ImportedPerson], members: Sequence[str] = ()
) -> tuple[list[ImportedPerson], Counter[str]]:
    """Fold the rows with one profile link into one row (the same link is the same person; a
    row without a link is never folded). Returns the survivors and the rows folded per
    member."""
    survivors: list[ImportedPerson] = []
    by_url: dict[str, int] = {}
    merged: Counter[str] = Counter()
    for i, item in enumerate(items):
        p = item.person
        url_key = (p.profile_url or "").casefold().rstrip("/")
        # only the same profile link is the same person: two rows with one name and title and
        # two links are two rows of the owner's sheet, kept as written (before, five people of
        # the UCB list were folded that way, 2026-11-05)
        at = by_url.get(url_key) if url_key else None
        if at is None:
            survivors.append(item)
            at = len(survivors) - 1
            if url_key:
                by_url[url_key] = at
            continue
        merged[members[i] if i < len(members) else ""] += 1
        have = survivors[at]
        second = have.second_profile_url
        if p.profile_url and p.profile_url.casefold().rstrip("/") != (
            have.person.profile_url or ""
        ).casefold().rstrip("/"):
            second = second or p.profile_url
        survivors[at] = ImportedPerson(
            person=have.person,
            note=have.note or item.note,
            second_profile_url=second or item.second_profile_url,
            merged_count=have.merged_count + 1,
            former_employers=have.former_employers or item.former_employers,
        )
    return survivors, merged


# --- tech -----------------------------------------------------------------------------------


def _int(value: str | None) -> int:
    digits = re.sub(r"[^\d]", "", value or "")
    return int(digits) if digits else 0


def _yes(value: str | None) -> bool | None:
    word = (value or "").strip().casefold()
    return True if word in ("yes", "true", "y") else False if word in ("no", "false", "n") else None


def _counted(spec: str | None) -> tuple[tuple[str, int | None], ...]:
    """``"Salesforce (93); Microsoft Excel (79)"`` as (name, count) tuples."""
    out: list[tuple[str, int | None]] = []
    for part in (spec or "").split(";"):
        part = part.strip()
        if not part:
            continue
        m = _COUNTED.match(part)
        out.append((m.group(1).strip()[:200], _int(m.group(2))) if m else (part[:200], None))
    return tuple(out)


@dataclass
class TechRows:
    """The three Tech Stack and Teams sheets, split by what their rows carry."""

    stack: list[ImportedStackItem] = field(default_factory=list)
    detections: list[ImportedDetection] = field(default_factory=list)
    teams: list[ImportedTeam] = field(default_factory=list)
    # the member each row came from, parallel to the lists above
    stack_members: list[str] = field(default_factory=list)
    detection_members: list[str] = field(default_factory=list)
    team_members: list[str] = field(default_factory=list)


def tech_of(found: Found, into: TechRows) -> None:
    """Sort one Tech Stack and Teams row into the stack, the ranking or the teams."""
    f = found.fields
    member = str(found.location.get("member") or "")
    if f.get("category") == "team":
        into.team_members.append(member)
        into.teams.append(
            ImportedTeam(
                team=found.value[:300],
                function=(f.get("function") or None),
                postings_count=_int(f.get("sources")),
                first_activity=_date(f.get("first_activity")),
                last_activity=_date(f.get("last_activity")),
                lane_technologies=_split(f.get("lanes")),
                technologies=_counted(f.get("technologies")),
                locations=_counted(f.get("locations")),
            )
        )
    elif f.get("rank") or f.get("open_now") or f.get("in_stack") or f.get("checked"):
        into.detection_members.append(member)
        into.detections.append(
            ImportedDetection(
                technology=found.value[:200],
                source_count=_int(f.get("sources")),
                last_seen_at=(f.get("last_seen") or "")[:10] or None,
                open_now=_int(f.get("open_now")) if f.get("open_now") else None,
                in_confirmed_stack=_yes(f.get("in_stack")),
                checked_by_reading=_yes(f.get("checked")),
            )
        )
    else:
        into.stack_members.append(member)
        into.stack.append(
            ImportedStackItem(
                business_function=(f.get("function") or ""),
                category=(f.get("category") or ""),
                technology=found.value[:200],
                postings=_int(f.get("sources")),
            )
        )


def merge_tech(rows: TechRows) -> tuple[TechRows, Counter[str]]:
    """One row per (function, category, technology), per platform and per team; returns the
    merged rows and how many were folded, per member."""
    merged: Counter[str] = Counter()
    out = TechRows()
    seen: set[tuple[str, ...]] = set()
    for item, member in zip(rows.stack, rows.stack_members, strict=False):
        skey = (
            "stack",
            item.business_function.casefold(),
            item.category.casefold(),
            item.technology.casefold(),
        )
        if skey in seen:
            merged[member] += 1
            continue
        seen.add(skey)
        out.stack.append(item)
    for det, member in zip(rows.detections, rows.detection_members, strict=False):
        dkey = ("det", det.technology.casefold())
        if dkey in seen:
            merged[member] += 1
            continue
        seen.add(dkey)
        out.detections.append(det)
    teams: dict[str, ImportedTeam] = {}
    for team, member in zip(rows.teams, rows.team_members, strict=False):
        tkey = team.team.casefold()
        if tkey in teams:
            merged[member] += 1
            have = teams[tkey]
            fuller = team if len(team.technologies) > len(have.technologies) else have
            teams[tkey] = ImportedTeam(
                team=have.team,
                function=have.function or team.function,
                postings_count=max(have.postings_count, team.postings_count),
                first_activity=min(d for d in (have.first_activity, team.first_activity) if d)
                if have.first_activity or team.first_activity
                else None,
                last_activity=max(d for d in (have.last_activity, team.last_activity) if d)
                if have.last_activity or team.last_activity
                else None,
                lane_technologies=tuple(
                    dict.fromkeys((*have.lane_technologies, *team.lane_technologies))
                ),
                technologies=fuller.technologies,
                locations=fuller.locations,
                merged_count=have.merged_count + 1,
            )
            continue
        teams[tkey] = team
    out.teams = list(teams.values())
    return out, merged


# --- the import -----------------------------------------------------------------------------


def _text_key(tenant_id: UUID, document_id: UUID, posting_key: str) -> str:
    digest = hashlib.sha256(posting_key.encode("utf-8")).hexdigest()[:32]
    return f"tenants/{tenant_id}/documents/{document_id}/postings/{digest}.txt"


async def import_workspace(
    txn: Any,
    tenant_id: UUID,
    doc: Any,
    roles: Mapping[str, MemberRole],
    *,
    now: datetime,
    payloads: Any | None = None,
    blobs: Any | None = None,
    only_parts: frozenset[str] | None = None,
) -> Counter[str]:
    """Read the document's row passages of its deliverable workbooks and store every row in its
    native table under the file-import run (see the module docstring). Returns the counts per
    kind: rows read, written and merged, plus the reader's skips. The import's status per part is
    recorded as it goes (``documents.import_status``); a failure before or between the parts
    marks the import run failed and is raised again. ``only_parts`` (the sweep's retry of failed
    parts) runs those parts only and leaves the others, and their rows, as they are."""
    from documents.deliverables import error_code

    track: dict[str, Any] = {}
    try:
        return await _import_workspace(
            txn,
            tenant_id,
            doc,
            roles,
            now=now,
            payloads=payloads,
            blobs=blobs,
            track=track,
            only_parts=only_parts,
        )
    except Exception as error:  # noqa: BLE001 - record the failure on the run, then raise it again
        if doc.account_id is not None:
            message = f"{type(error).__name__} ({error_code(error)})"
            try:
                if track.get("run") is None:
                    await import_status.record_loose(
                        txn,
                        tenant_id,
                        document_id=UUID(str(doc.id)),
                        account_id=UUID(str(doc.account_id)),
                        owner=track.get("doc_owner"),
                        error=message,
                    )
                else:
                    await import_status.finish_run(
                        txn, tenant_id, track["run"], track.get("doc_owner"), error=message
                    )
            except Exception as status_error:  # noqa: BLE001 - never mask the import's own error
                _log.warning(
                    "documents.import_status_failed",
                    extra={"error_class": type(status_error).__name__},
                )
        raise


async def _import_workspace(
    txn: Any,
    tenant_id: UUID,
    doc: Any,
    roles: Mapping[str, MemberRole],
    *,
    now: datetime,
    payloads: Any | None,
    blobs: Any | None,
    track: dict[str, Any],
    only_parts: frozenset[str] | None = None,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    if doc.account_id is None:
        return counts
    account_id = UUID(str(doc.account_id))
    document_id = UUID(str(doc.id))
    reader = SheetReader(roles=dict(roles))
    found: dict[str, list[Found]] = {"jobs": [], "people": [], "tech": []}
    rows_seen: Counter[str] = Counter()  # whole rows per importable member (a row's parts once)
    docx_rows: list[Any] = []
    docx_sections: list[Any] = []
    after = -1
    # LOOP(workspace import pages): cap=passages / PAGE pages; exit=a short page
    while True:
        async with txn(tenant_id) as s:
            page = await repo.passages_after(s, doc.id, after, PAGE)
        if not page:
            break
        after = int(page[-1].seq)
        for r in page:
            location = dict(r.location or {})
            member = str(member_of_location(location) or "")
            if roles.get(member) in DOCX_ROLES:
                # the Word deliverables: their tables and prose go to the Word planner
                if r.kind == "crm_row":
                    docx_rows.append(r)
                elif r.kind == "section":
                    docx_sections.append(r)
                continue
            if r.kind != "crm_row":
                continue
            if int(location.get("part") or 1) == 1 and may_import(roles, member):
                rows_seen[member] += 1
            for item in reader.read(r):
                found.setdefault(item.kind, []).append(item)
        if len(page) < PAGE:
            break
    counts.update(reader.skipped)
    from account_intelligence.tracker.config import load_checklist
    from documents import workspace_docx, workspace_identity, workspace_raw

    docx_plan = workspace_docx.plan(docx_rows, docx_sections, load_checklist().rows)

    def member_of(x: Found) -> str:
        """The zip member a found row came from."""
        return str(x.location.get("member") or "")

    # the package's raw members, read once: its identity (which company, which day the pulls
    # were made), the raw pulls and notes (phase 4) and the posting feed the Jobs sheet's
    # archive records came from
    zip_bytes: bytes | None = None
    members: dict[str, bytes] = {}
    identity = workspace_identity.WorkspaceIdentity()
    files: dict[str, bytes] = {}  # the package's deliverable files, registered as the account's
    if blobs is not None and getattr(doc, "object_key", None):
        with blobs.open(str(doc.object_key)) as stream:
            zip_bytes = stream.read()
        members = workspace_raw.read_members(zip_bytes, roles)
        files = workspace_raw.deliverable_members(zip_bytes, roles)
        identity = workspace_identity.identity_of(members, roles)
    research_date = identity.research_date
    # the rows are dated by the research, not by the upload: a package pulled on 24 October
    # and uploaded on 4 November was collected on 24 October (UCB, 2026-11-05)
    collected = datetime.combine(research_date, time(12, 0), tzinfo=UTC) if research_date else now
    counts["research_date"] = int(research_date is not None)

    # a posting row needs a web address (the table's rule, as for a research's postings). The
    # owner's sheet links only the postings seen live; a closed posting with a requisition
    # gets its address from the careers site's pattern, a feed record from the feed in the
    # zip (title, location and first seen); a row with none of these is listed as not imported
    linkless = [x for x in found["jobs"] if not x.fields.get("url")]
    if linkless:
        template = careers_url_template(found["jobs"])
        feed = workspace_raw.feed_postings(zip_bytes, roles) if zip_bytes else []
        counts["jobs_links_from_pattern"], counts["jobs_links_from_feed"] = fill_posting_links(
            linkless, template, feed
        )
    # a row that still has no address is kept as written (migration 0111): the owner's sheet
    # lists it, so the table lists it
    no_url = [x for x in found["jobs"] if not x.fields.get("url")]
    counts["jobs_no_url"] = len(no_url)
    postings_raw = [posting_of(x, document_id=document_id, now=collected) for x in found["jobs"]]
    postings, jobs_merged = keep_rows(postings_raw, found["jobs"])
    people_members = [member_of(x) for x in found["people"]]
    people_raw = [
        person_of(x, document_id=document_id, notes=docx_plan.notes) for x in found["people"]
    ]
    people, people_merged = merge_people(people_raw, people_members)
    tech_raw = TechRows()
    for x in found["tech"]:
        tech_of(x, tech_raw)
    tech, tech_merged = merge_tech(tech_raw)
    counts.update(
        {
            "jobs_rows": len(postings_raw),
            "jobs_merged": sum(jobs_merged.values()),
            "people_rows": len(people_raw),
            "people_merged": sum(people_merged.values()),
            "tech_rows": len(found["tech"]),
            "tech_merged": sum(tech_merged.values()),
        }
    )
    has_docx = bool(docx_plan.sources or docx_plan.facts or docx_plan.sections or docx_plan.tracker)
    # the captured pages, raw pulls and notes, read from the zip itself (phase 4)
    raw_plan = None
    if zip_bytes is not None:
        async with txn(tenant_id) as s:
            acct_row = (
                await s.execute(
                    text("SELECT domain FROM accounts WHERE id = :a"), {"a": account_id}
                )
            ).first()
        raw_plan = workspace_raw.plan(
            members,
            roles,
            document_id=document_id,
            account_domain=str(acct_row.domain) if acct_row else "",
        )
        counts.update({f"raw_{k}": v for k, v in raw_plan.counts.items()})
    has_raw = raw_plan is not None and bool(raw_plan.records)
    has_tech = bool(tech.stack or tech.detections or tech.teams)

    async with txn(tenant_id) as s:
        doc_owner = (
            await s.execute(
                text("SELECT owner_user_id FROM documents WHERE tenant_id = :t AND id = :d"),
                {"t": tenant_id, "d": document_id},
            )
        ).scalar_one_or_none()
        # the rows are owned like the account (private workspaces): write as the account's
        # owner, not as the uploader, so the owner-scoped tables (the source counter among
        # them) see them
        owner = (
            await s.execute(
                text("SELECT owner_user_id FROM accounts WHERE tenant_id = :t AND id = :a"),
                {"t": tenant_id, "a": account_id},
            )
        ).scalar_one_or_none()
    doc_owner = str(doc_owner) if doc_owner else None
    track["doc_owner"] = doc_owner

    def skipped_of(kind: str) -> int:
        return sum(counts.get(f"{kind}_{k}", 0) for k in _READER_SKIPS)

    def reader_reasons(kind: str) -> list[str]:
        return list(
            import_status.reasons_from_counts(
                {k: counts.get(f"{kind}_{k}", 0) for k in _READER_SKIPS}, _READER_SKIPS
            )
        )

    skip_info = {
        "jobs": (skipped_of("jobs"), reader_reasons("jobs")[: import_status.MAX_REASONS]),
        "people": (skipped_of("people"), reader_reasons("people")),
        "tech": (skipped_of("tech"), reader_reasons("tech")),
    }
    will_run = {
        "jobs": bool(postings),
        "people": bool(people),
        "tech": has_tech,
        "docx": has_docx,
        "raw": has_raw,
    }
    if only_parts is not None:
        # a retry of the parts that failed: the parts that succeeded are not run again
        will_run = {part: run and part in only_parts for part, run in will_run.items()}
    import_run_id = await import_status.begin(
        txn,
        tenant_id,
        document_id=document_id,
        account_id=account_id,
        owner=doc_owner,
        parts={
            part: (will_run[part], *skip_info.get(part, (0, []))) for part in import_status.PARTS
        },
        only=only_parts,
    )
    track["run"] = import_run_id
    with log_context(
        tenant_id=tenant_id,
        account_id=account_id,
        document_id=document_id,
        import_run_id=import_run_id,
    ):
        if not any(will_run.values()):
            await import_status.finish_run(txn, tenant_id, import_run_id, doc_owner)
            return counts

        # the posting text goes to the payload store so a rebuilt workbook prints it
        if payloads is not None:
            descriptions: dict[str, str] = {}
            for x, raw in zip(found["jobs"], postings_raw, strict=True):
                body = x.fields.get("description")
                pkey = raw.posting.posting_key
                if body and (pkey not in descriptions or len(body) > len(descriptions[pkey])):
                    descriptions[pkey] = body
            stored: list[ImportedPosting] = []
            for kept in postings:
                body = descriptions.get(kept.posting.posting_key)
                if body:
                    key = _text_key(tenant_id, document_id, kept.posting.posting_key)
                    await payloads.put(key, body.encode("utf-8"))
                    kept = ImportedPosting(
                        posting=kept.posting.model_copy(update={"text_ref": key}),
                        entity_tag=kept.entity_tag,
                        summary_read=kept.summary_read,
                        locations=kept.locations,
                        merged_count=kept.merged_count,
                    )
                stored.append(kept)
            postings = stored

        source_ref = f"document:{document_id}"
        merged_by_member = jobs_merged + people_merged + tech_merged
        found_by_member = Counter(member_of(x) for kind in found.values() for x in kind)
        from account_intelligence.db.session import acting_as

        with acting_as(str(owner)) if owner else contextlib.nullcontext():
            await _write_all(
                txn, tenant_id, account_id, document_id, doc, roles, now, postings, people, tech,
                docx_plan, has_docx, source_ref, counts, rows_seen, found_by_member,
                merged_by_member, raw_plan=raw_plan, payloads=payloads,
                import_run_id=import_run_id, doc_owner=doc_owner, will_run=will_run,
                skip_info=skip_info, research_date=research_date, collected=collected,
                files=files,
            )  # fmt: skip
        await import_status.finish_run(txn, tenant_id, import_run_id, doc_owner)
    return counts


_READER_SKIPS = {
    "no_key": "no name or title",
    "key_not_plausible": "name not plausible",
}


# deliverables.kind of each deliverable role (a DELIVERABLE_OTHER file has no kind: not registered)
FILE_KINDS: Mapping[MemberRole, str] = {
    MemberRole.DELIVERABLE_SUMMARY: "summary",
    MemberRole.DELIVERABLE_EVIDENCE: "evidence",
    MemberRole.DELIVERABLE_PEOPLE: "people",
    MemberRole.DELIVERABLE_JOBS: "jobs",
    MemberRole.DELIVERABLE_TECH: "tech",
}


_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    """A file name the ``deliverables`` row accepts (letters, digits, ``._-``, starting with a
    letter or digit, at most 151 characters): "UCB People List.xlsx" becomes
    "UCB_People_List.xlsx", "&" becomes "and". The name as uploaded is kept in the manifest
    (``original_name``) and Download everything ships the file under it."""
    stem, dot, ext = name.replace("&", " and ").rpartition(".")
    if not dot:
        stem, ext = name, ""
    clean = _UNSAFE_NAME.sub("_", stem).strip("._-") or "file"
    ext = _UNSAFE_NAME.sub("", ext)[:10]
    return (clean[: 150 - len(ext)] + (f".{ext}" if ext else ""))[:151]


def sniff_kind(member: str, body: bytes) -> str | None:
    """The deliverable kind a workbook is by its sheets, for a file whose name says nothing
    ("export.xlsx", owner 2026-11-05: later packages differ a little): the kind of the sheet with
    the most rows whose header (one of its first ``HEADER_SEARCH_ROWS`` rows) the import
    understands (``deliverables.match``); a teams sheet counts as tech. None when no sheet is
    understood, the file is not a workbook, or it cannot be opened."""
    from openpyxl import load_workbook

    from documents.deliverables import HEADER_SEARCH_ROWS, match

    if not member.lower().endswith(".xlsx"):
        return None
    best: tuple[int, str] | None = None
    try:
        book = load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 - an unreadable workbook is simply not sniffed
        return None
    try:
        for ws in book.worksheets:
            kind: str | None = None
            rows = 0
            for n, values in enumerate(ws.iter_rows(values_only=True), start=1):
                if kind is None:
                    if n > HEADER_SEARCH_ROWS:
                        break
                    cells = [str(v).strip() for v in values if v is not None and str(v).strip()]
                    found = match(cells) if len(cells) > 1 else None
                    kind = found.kind if found is not None else None
                    continue
                rows += any(v is not None for v in values)
            if kind is not None and (best is None or rows > best[0]):
                best = (rows, kind)
    finally:
        book.close()
    return best[1] if best else None


# a name that says the file is an older or spare copy: ranked after every other file of its kind
_SPARE_COPY = re.compile(
    r"(?i)(^|[^a-z])(old|backup|bak|copy|draft|previous|prev|archive)([^a-z]|$)"
)


def choose_files(
    files: Mapping[str, bytes], roles: Mapping[str, MemberRole]
) -> dict[str, tuple[str, bytes]]:
    """The one file registered for each deliverable kind (owner 2026-11-05: a later package may
    hold a renamed file, an unnamed export, or an old copy beside the current one). A workbook's
    kind is what its sheets hold when they say (``sniff_kind``), else what its name says; a Word
    file's kind is its name's. Among the files of one kind the choice is fixed, never the zip's
    order: the product's exact name first, then a name in words, then a file known only by its
    sheets; a name marking an old or spare copy ("old", "backup", "copy", "draft") after the
    rest; then the shallower path; then the name. Returns kind -> (member, bytes)."""
    from documents.workspace import _DELIVERABLE_SUFFIXES

    ranked: dict[str, list[tuple[tuple[int, int, int, str], str, bytes]]] = {}
    for member, body in files.items():
        if not body:
            continue
        role = roles.get(member, MemberRole.OTHER)
        named = FILE_KINDS.get(role)
        sniffed = sniff_kind(member, body)
        kind = sniffed or named
        if kind is None:
            continue
        base = member.rsplit("/", 1)[-1]
        if (
            named
            and kind == named
            and base.lower().endswith(tuple(suffix for suffix, _ in _DELIVERABLE_SUFFIXES))
        ):
            source = 0
        elif named and kind == named:
            source = 1
        else:
            source = 2  # known by its sheets only, or its sheets overrule its name
        spare = 1 if _SPARE_COPY.search(posixpath.splitext(base)[0]) else 0
        ranked.setdefault(kind, []).append(
            ((spare, source, member.count("/"), member), member, body)
        )
    chosen: dict[str, tuple[str, bytes]] = {}
    for kind, options in ranked.items():
        options.sort(key=lambda o: o[0])
        _rank, member, body = options[0]
        chosen[kind] = (member, body)
        if len(options) > 1:
            _log.warning(
                "documents.import_duplicate_kind",
                extra={"kind": kind, "used": member, "not_used": [o[1] for o in options[1:]]},
            )
    return chosen


async def register_deliverables(
    s: Any,
    tenant_id: UUID,
    account_id: UUID,
    run_id: UUID,
    document_id: UUID,
    files: Mapping[str, bytes],
    roles: Mapping[str, MemberRole],
    payloads: Any,
    now: datetime,
) -> int:
    """Register the package's deliverable files as the account's delivered files under the
    file-import run, byte for byte: one ``deliverables`` row per file (the next version of its
    kind), its bytes in the payload store under the export store's key. The manifest says
    ``source: workspace_import`` so the screens show the file under any exclusion set and never
    rebuild or materialise it (``replica.file_tabs.fresh``, ``replica.sheets.imported_run``).
    A second import of the same document (the same bytes) registers nothing again. Returns the
    files registered."""
    from exports.store import insert_imported, object_key, put_if_absent
    from views.materialize import current_exclusion_hash

    chosen = choose_files(files, roles)
    if not chosen:
        return 0
    shas = {kind: hashlib.sha256(body).hexdigest() for kind, (_m, body) in chosen.items()}
    set_hash = hashlib.sha256("".join(sorted(shas.values())).encode()).hexdigest()
    exclusion_hash = await current_exclusion_hash(s)
    await s.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"deliverables:{account_id}"}
    )
    written = 0
    for kind, (member, body) in chosen.items():
        exists = await s.scalar(
            text(
                "SELECT 1 FROM deliverables WHERE tenant_id = :t AND run_id = :r"
                " AND set_hash = :h AND kind = :k"
            ),
            {"t": tenant_id, "r": run_id, "h": set_hash, "k": kind},
        )
        if exists:
            continue
        key = object_key(str(tenant_id), str(account_id), str(run_id), kind, shas[kind])
        await put_if_absent(payloads, key, body)
        original = member.rsplit("/", 1)[-1]
        manifest = {
            "source": "workspace_import",
            "document_id": str(document_id),
            "member": member,
            "original_name": original,
            "exclusion_hash": exclusion_hash,
            "files_withheld": [],
        }
        await insert_imported(
            s,
            tenant_id=tenant_id,
            account_id=account_id,
            run_id=run_id,
            set_hash=set_hash,
            kind=kind,
            filename=safe_filename(original),
            sha256=shas[kind],
            size_bytes=len(body),
            key=key,
            manifest=manifest,
            built_at=now,
        )
        written += 1
    return written


async def _part(
    txn: Any,
    tenant_id: UUID,
    import_run_id: UUID,
    doc_owner: str | None,
    part: str,
    counts: Counter[str],
    document_id: UUID,
    write: Any,
    *,
    skipped: int,
    reasons: Sequence[str],
) -> None:
    """One part of the import in its own transaction, its status in two more (running before,
    the outcome after); a failure rolls back that part only and is recorded, not raised."""
    from documents.deliverables import error_code

    await import_status.part_started(txn, tenant_id, import_run_id, part, doc_owner)
    delta: Counter[str] = Counter()
    error_text: str | None = None
    with log_context(part=part):
        try:
            async with txn(tenant_id) as s:
                delta = await write(s)
            counts.update(delta)
        except Exception as error:  # noqa: BLE001 - one part never stops the others
            counts[f"{part}_failed"] += 1
            error_text = f"{type(error).__name__} ({error_code(error)})"
            _log.warning(
                "documents.import_part_failed",
                extra={
                    "document_id": str(document_id),
                    "part": part,
                    "error_class": type(error).__name__,
                    "error_code": error_code(error)[:80],
                },
            )
        await import_status.part_finished(
            txn,
            tenant_id,
            import_run_id,
            part,
            doc_owner,
            status=import_status.status_of(failed=error_text is not None, had_input=True),
            # the writers' ``*_written`` counters only (a part also counts merges and skips)
            rows_written=0
            if error_text
            else sum(v for k, v in delta.items() if k.endswith("_written")),
            rows_skipped=skipped,
            reasons=reasons,
            error=error_text,
        )
        _log.info("documents.import_part_done", extra={"outcome": "failed" if error_text else "ok"})


async def _write_all(
    txn: Any,
    tenant_id: UUID,
    account_id: UUID,
    document_id: UUID,
    doc: Any,
    roles: Mapping[str, MemberRole],
    now: datetime,
    postings: Sequence[ImportedPosting],
    people: Sequence[ImportedPerson],
    tech: TechRows,
    docx_plan: Any,
    has_docx: bool,
    source_ref: str,
    counts: Counter[str],
    rows_seen: Mapping[str, int],
    found_by_member: Mapping[str, int],
    merged_by_member: Mapping[str, int],
    *,
    import_run_id: UUID,
    doc_owner: str | None,
    will_run: Mapping[str, bool],
    skip_info: Mapping[str, tuple[int, Sequence[str]]],
    raw_plan: Any = None,
    payloads: Any | None = None,
    research_date: date | None = None,
    collected: datetime | None = None,
    files: Mapping[str, bytes] | None = None,
) -> None:
    """Every write of the import. Each part runs in its own transaction (and its own status
    rows): a row one part cannot store (a bad address in the Summary's source links, UCB
    2026-11-04) is logged and counted ``<part>_failed``, and the other parts' rows still land,
    instead of the whole upload importing nothing. ``research_date`` and ``collected`` are the
    day the package's pulls were made (``documents.workspace_identity``), when it says."""
    from account_intelligence.runs import lifecycle
    from documents import workspace_docx, workspace_raw

    async with txn(tenant_id) as s0:
        run_id = await lifecycle.record_file_import_run(
            s0, tenant_id, account_id, document_id, now=now, research_date=research_date
        )
    await import_status.set_file_import_run(txn, tenant_id, import_run_id, run_id, doc_owner)
    if files and payloads is not None and roles:
        # the package's own files become the account's delivered files, byte for byte: the
        # screens open them as they are and Download everything ships them (exact copy)
        try:
            async with txn(tenant_id) as s0:
                counts["files_registered"] = await register_deliverables(
                    s0, tenant_id, account_id, run_id, document_id, files, roles, payloads, now
                )
        except Exception as error:  # noqa: BLE001 - the rows still import; the failure is logged
            counts["files_failed"] += 1
            _log.warning(
                "documents.import_files_failed",
                extra={"document_id": str(document_id), "error_class": type(error).__name__},
            )

    async def jobs(s: Any) -> Counter[str]:
        return Counter(
            jobs_written=await import_postings(s, tenant_id, account_id, run_id, postings)
        )

    async def people_part(s: Any) -> Counter[str]:
        return Counter(people_written=await import_people(s, tenant_id, account_id, run_id, people))

    async def tech_part(s: Any) -> Counter[str]:
        written = await import_stack_items(s, tenant_id, account_id, run_id, tech.stack, source_ref)
        written += await import_detections(
            s, tenant_id, account_id, run_id, tech.detections, source_ref
        )
        teams = await import_platform_teams(
            s, tenant_id, account_id, run_id, tech.teams, source_ref
        )
        return Counter(tech_written=written, teams_written=teams)

    async def docx_part(s: Any) -> Counter[str]:
        acct = (
            await s.execute(
                text("SELECT domain, name FROM accounts WHERE tenant_id = :t AND id = :a"),
                {"t": tenant_id, "a": account_id},
            )
        ).first()
        return Counter(
            await workspace_docx.write(
                s,
                tenant_id=tenant_id,
                account_id=account_id,
                run_id=run_id,
                document_id=document_id,
                docx_plan=docx_plan,
                authoritative=bool(doc.authoritative) or _own_package(roles),
                account_domain=str(acct.domain) if acct else "",
                account_name=(str(acct.name) if acct and acct.name else None),
                now=now,
            )
        )

    async def raw_part(s: Any) -> Counter[str]:
        acct_raw = (
            await s.execute(
                text("SELECT domain, name FROM accounts WHERE tenant_id = :t AND id = :a"),
                {"t": tenant_id, "a": account_id},
            )
        ).first()
        return Counter(
            await workspace_raw.write(
                s,
                txn=txn,
                tenant_id=tenant_id,
                account_id=account_id,
                run_id=run_id,
                document_id=document_id,
                raw_plan=raw_plan,
                payloads=payloads,
                now=collected or now,
                account_domain=str(acct_raw.domain) if acct_raw else "",
                account_name=(str(acct_raw.name) if acct_raw and acct_raw.name else None),
                visibility="public"
                if (bool(doc.authoritative) or _own_package(roles))
                else "private",
            )
        )

    writers = {
        "jobs": jobs,
        "people": people_part,
        "tech": tech_part,
        "docx": docx_part,
        "raw": raw_part,
    }
    for part in import_status.PARTS:
        if will_run[part]:
            skipped, reasons = skip_info.get(part, (0, []))
            await _part(
                txn, tenant_id, import_run_id, doc_owner, part, counts, document_id,
                writers[part], skipped=skipped, reasons=reasons,
            )  # fmt: skip
    # the reconciliation per member: file rows = imported + merged + skipped. The members are owned
    # like the document (its uploader), who may not be the account's owner the parts act as
    with import_status.acting_owner(doc_owner):
        async with txn(tenant_id) as s:
            for member in set(rows_seen) | set(found_by_member):
                merged = merged_by_member.get(member, 0)
                imported = found_by_member.get(member, 0) - merged
                skipped = max(0, rows_seen.get(member, 0) - found_by_member.get(member, 0))
                await repo.set_member_import_counts(
                    s, document_id, member, imported, merged, skipped
                )
