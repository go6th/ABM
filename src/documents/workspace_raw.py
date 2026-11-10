"""Workspace import, phase 4 (2026-11-03): the captured pages, raw pulls and notes of an
uploaded workspace zip into the tables that hold a research's own copies and records.

Read straight from the zip (``read_members``: the member bytes by role, the big posting feed
left out because the Job Postings workbook already carried the postings), then:

* ``data/releases/*.md`` (a captured page: ``title:``, ``url:``, ``date:`` header lines, then
  the page text) -> ``link_archive`` through ``account_intelligence.archive.LinkArchive`` as a
  ``page-archive/1`` record (a careers-site page as kind ``posting``), the body in the payload
  store, so the Archive page lists it and a sheet's link opens the copy;
* ``techstack_detections.json`` -> ``tech_detections`` with score, where seen, behind
  firewall and locations; ``techstack_confirmed.json`` -> ``tech_stack_items``; ``teams.json``
  -> ``platform_teams``; ``news_events.json`` -> ``news_events``; ``connections.json`` ->
  ``org_connections``; ``profiles_full.json`` -> ``person_profiles`` of the people the import
  holds; ``posts_comments.json`` -> ``person_posts`` and ``person_comments``;
  ``leadership.json`` -> ``people`` (named on the leadership page, for review); the company
  record -> ``facts`` (ticker, headquarters, employee count);
* ``notes/*.md`` and ``RESTORE.md`` -> ``research_notes``; the pull log's call table ->
  ``pull_log_calls``.

Each member's row on the Uploaded files sheet shows records in the file = imported + merged +
skipped. No model is called. The search files, the live board listing and the people grid are
left as tables for Ask (their rows are not records of the account).

GUARANTEE(test_a_workspace_zip_imports_each_deliverable_row_once_and_skips_build_files)
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text

from agents.production.pipelines import (
    ImportedConnection,
    ImportedDetection,
    ImportedNewsEvent,
    ImportedStackItem,
    ImportedTeam,
    import_connections,
    import_detections,
    import_news_events,
    import_platform_teams,
    import_stack_items,
)
from documents import repo
from documents.workspace import MemberRole
from people.models import Classification as PersonClassification
from people.models import Person
from people.repo import (
    ImportedPerson,
    ImportedPost,
    ImportedProfile,
    import_people,
    import_posts,
    import_profiles,
    known_sources,
    record_folded_sources,
)

RAW_MAX_BYTES = 64 * 1024 * 1024
SKIP_PULLS = re.compile(
    r"(^|/)(jobs_[a-z0-9_]*|people_grid[a-z_]*|live_board|subsidiary_domains|releases_gap[a-z_0-9]*"
    r"|transcripts_index|people_gapcheck|org_signals)\.(json|txt)$"
)
# the posting feeds the Jobs sheet's archive records came from (``feed_postings``): read on their
# own, only to give a linkless workbook row its web address
FEED_PULLS = re.compile(r"(^|/)jobs_[a-z0-9_]*\.json$")
# a captured page's header lines: ``title:``/``url:``/``date:`` as the synthetic packages write
# them, ``Source URL:``/``Captured:``/``Listing metadata:`` as the owner's packages do (2026-11-05)
_HEADER = re.compile(
    r"^(title|url|source url|date|captured|source|query|listing metadata):\s*(.*)$", re.IGNORECASE
)
_HEADER_KEYS = {"source url": "url", "captured": "captured", "listing metadata": "listing"}
_MEMBER_DATE = re.compile(r"(?:^|/)(\d{4}-\d{2}-\d{2})_")
_MARKDOWN_HEADING = re.compile(r"^#{1,3}\s+(.+?)\s*$")
_NOTE_KINDS = (("pull_log", "pull_log"), ("pass1", "pass1"), ("sense_check", "sense_check"))


@dataclass
class RawPlan:
    """What the raw members become, per member (see the module docstring)."""

    pages: list[dict[str, Any]] = field(default_factory=list)
    detections: list[tuple[str, ImportedDetection]] = field(default_factory=list)
    stack: list[tuple[str, ImportedStackItem]] = field(default_factory=list)
    teams: list[tuple[str, ImportedTeam]] = field(default_factory=list)
    news: list[tuple[str, ImportedNewsEvent]] = field(default_factory=list)
    connections: list[tuple[str, ImportedConnection]] = field(default_factory=list)
    profiles: list[tuple[str, ImportedProfile]] = field(default_factory=list)
    posts: list[tuple[str, ImportedPost]] = field(default_factory=list)
    comments: list[tuple[str, ImportedPost]] = field(default_factory=list)
    people: list[tuple[str, ImportedPerson]] = field(default_factory=list)
    company_facts: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    searches: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    records: Counter[str] = field(default_factory=Counter)  # records read per member
    counts: Counter[str] = field(default_factory=Counter)


# --- reading the zip ------------------------------------------------------------------------


def read_members(
    stream: Any, roles: Mapping[str, MemberRole], *, max_bytes: int = RAW_MAX_BYTES
) -> dict[str, bytes]:
    """The bytes of the captured pages, raw pulls, notes and restore files of a workspace zip,
    by member path; a pull the import does not read (``SKIP_PULLS``) or past ``max_bytes`` is
    left out."""
    wanted = {
        MemberRole.CAPTURED_PAGE,
        MemberRole.RAW_PULL,
        MemberRole.NOTE,
        MemberRole.RESTORE,
    }
    out: dict[str, bytes] = {}
    data = stream.read() if hasattr(stream, "read") else stream
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            role = roles.get(info.filename)
            if info.is_dir() or role not in wanted:
                continue
            if role is MemberRole.RAW_PULL and SKIP_PULLS.search(info.filename):
                continue
            if info.file_size > max_bytes:
                continue
            out[info.filename] = archive.read(info)
    return out


def deliverable_members(
    stream: Any, roles: Mapping[str, MemberRole], *, max_bytes: int = RAW_MAX_BYTES
) -> dict[str, bytes]:
    """The bytes of the package's deliverable files (the five client files and any other
    deliverable), by member path, in zip order: the import registers them as the account's
    files, byte for byte (exact copy, 2026-11-05)."""
    from documents.workspace import DELIVERABLE_ROLES

    out: dict[str, bytes] = {}
    data = stream.read() if hasattr(stream, "read") else stream
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.is_dir() or roles.get(info.filename) not in DELIVERABLE_ROLES:
                continue
            if info.file_size > max_bytes:
                continue
            out[info.filename] = archive.read(info)
    return out


# --- parsers (pure) --------------------------------------------------------------------------


def _day(value: Any) -> date | None:
    text_value = str(value or "")[:10]
    try:
        return date.fromisoformat(text_value) if len(text_value) == 10 else None
    except ValueError:
        return None


def _loads(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def _records(data: Any, *keys: str) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            if isinstance(data.get(key), list):
                return list(data[key])
    return []


def _is_jsonapi(data: Any) -> bool:
    """A JSON:API document: ``data`` is a list of ``{id, type, attributes}`` resources, the
    related resources under ``included`` (the feeds of a workspace package, 2026-11-05)."""
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        return False
    first = next((r for r in data["data"] if isinstance(r, dict)), None)
    return first is not None and isinstance(first.get("attributes"), dict) and "type" in first


def _included(data: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """The included resources by (type, id), each as its attributes."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for item in data.get("included") or []:
        if isinstance(item, dict) and item.get("id") is not None and item.get("type"):
            raw_attrs = item.get("attributes")
            attrs: dict[str, Any] = dict(raw_attrs) if isinstance(raw_attrs, dict) else {}
            out[(str(item["type"]), str(item["id"]))] = attrs
    return out


def _related(resource: Mapping[str, Any], name: str) -> list[tuple[str, str]]:
    """The (type, id) pairs a resource's relationship points at (one or many)."""
    raw_rel = resource.get("relationships")
    rel: dict[str, Any] = raw_rel if isinstance(raw_rel, dict) else {}
    raw_link = rel.get(name)
    link: dict[str, Any] = raw_link if isinstance(raw_link, dict) else {}
    target = link.get("data")
    items = target if isinstance(target, list) else ([target] if isinstance(target, dict) else [])
    return [(str(t.get("type")), str(t.get("id"))) for t in items if isinstance(t, dict)]


def _jsonapi_rows(data: Mapping[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Each resource's attributes (with its ``id``) and the resource itself."""
    out = []
    for r in data.get("data") or []:
        if not isinstance(r, dict):
            continue
        attrs = dict(r.get("attributes") or {}) if isinstance(r.get("attributes"), dict) else {}
        attrs.setdefault("id", r.get("id"))
        out.append((attrs, r))
    return out


def feed_postings(
    data: bytes, roles: Mapping[str, MemberRole], *, max_bytes: int = RAW_MAX_BYTES
) -> list[dict[str, Any]]:
    """The posting feed records of the zip (``data/jobs_*.json``): title, location, first seen
    day, status and web address of each, so a Jobs sheet row the owner left without a link (an
    archive record) can carry the feed's address (UCB, 2026-11-05)."""
    out: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.is_dir() or roles.get(info.filename) is not MemberRole.RAW_PULL:
                continue
            if not FEED_PULLS.search(info.filename) or info.file_size > max_bytes:
                continue
            loaded = _loads(archive.read(info))
            for rec in _records(loaded, "jobs", "data", "postings", "records"):
                if not isinstance(rec, Mapping):
                    continue
                url = str(rec.get("url") or rec.get("job_url") or rec.get("link") or "")
                title = str(rec.get("title") or "")
                if not url.startswith("http") or not title:
                    continue
                out.append(
                    {
                        "title": title,
                        "location": str(rec.get("location") or ""),
                        "first_seen": str(rec.get("first_seen_at") or rec.get("first_seen") or "")[
                            :10
                        ],
                        "status": str(rec.get("status") or ""),
                        "url": url,
                    }
                )
    return out


def searches_of(data: Any) -> list[dict[str, Any]]:
    """The web searches a research ran (``web_searches.json``: ``query``, ``group``,
    ``result_count``, ``top_results``), one record per query, as the Web searches table reads
    them: query, purpose, pages returned and the sites the results came from."""
    out: list[dict[str, Any]] = []
    for rec in _records(data, "searches", "queries", "data"):
        if not isinstance(rec, Mapping):
            continue
        query = " ".join(str(rec.get("query") or rec.get("q") or "").split())
        if not query:
            continue
        results = rec.get("top_results") or rec.get("results") or []
        sites: list[str] = []
        pages: list[dict[str, str]] = []
        for item in results if isinstance(results, list) else []:
            url = str((item or {}).get("url") or "") if isinstance(item, Mapping) else str(item)
            if not url.startswith("http"):
                continue
            try:
                host = (urlsplit(url).hostname or "").lower()
            except ValueError:
                continue  # a malformed address in a search result is no site
            host = host[4:] if host.startswith("www.") else host
            if host and host not in sites:
                sites.append(host)
            pages.append({"url": url[:2000], "title": str((item or {}).get("title") or "")[:300]})
        count = rec.get("result_count")
        try:
            pages_total = int(count) if count is not None else len(pages)
        except (TypeError, ValueError):
            pages_total = len(pages)
        out.append(
            {
                "query": query[:500],
                "purpose": str(rec.get("group") or rec.get("purpose") or "")[:80],
                "pages": max(pages_total, 0),
                "sites": sites[:50],
                "results": pages[:50],
                "position": rec.get("id"),
            }
        )
    return out


def captured_page(text_body: str) -> tuple[dict[str, str], str]:
    """The header lines of a captured page (``title``, ``url``, ``date``, ``source``,
    ``query``) and the page text after them."""
    meta: dict[str, str] = {}
    lines = text_body.splitlines()
    body_from = len(lines)
    for i, line in enumerate(lines):
        m = _HEADER.match(line.strip())
        if m is None:
            body_from = i
            break
        key = m.group(1).lower()
        meta[_HEADER_KEYS.get(key, key)] = m.group(2).strip()
    body = "\n".join(lines[body_from:]).strip()
    if not meta.get("title"):
        # the owner's captures carry no title line: the page's first heading is its title
        heading = next(
            (h.group(1) for h in (_MARKDOWN_HEADING.match(x) for x in body.splitlines()) if h),
            "",
        )
        if heading:
            meta["title"] = heading.strip("# ").strip()
    return meta, body


def member_title(member: str) -> str:
    """A page title from a captured member's file name (``2026-10-22_web_ucb-guidance.md`` ->
    ``Ucb Guidance``) for a capture with neither a title line nor a heading."""
    stem = member.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}_", "", stem)
    stem = re.sub(r"^(?:web|gap|p2|events|careers|live_\d+)_", "", stem)
    words = [w for w in re.split(r"[-_]+", stem) if w]
    return " ".join(w[:1].upper() + w[1:] for w in words)


def member_date(member: str) -> str | None:
    """The day a captured member's file name carries (``2026-10-22_...``), else None (a
    ``0000-00-00`` prefix means the page is undated)."""
    m = _MEMBER_DATE.search(member)
    day = m.group(1) if m else None
    return day if day and day != "0000-00-00" and _day(day) else None


def page_kind(url: str, member: str) -> str:
    """``posting`` for a live posting capture (a ``_live_`` member or a jobs host), else
    ``page``."""
    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0].casefold()
    if "_live_" in member.rsplit("/", 1)[-1] or host.startswith(("jobs.", "careers.")):
        return "posting"
    return "page"


def _flatten_detections(data: Any) -> list[dict[str, Any]]:
    """Detection records as flat dicts, whichever shape the pull has: a list of
    ``{technology, ...}`` records, or the JSON:API feed whose technology name sits in the
    included ``technology`` resource (the UCB pull, 2026-11-05)."""
    if not _is_jsonapi(data):
        return [r for r in _records(data, "detections", "technologies") if isinstance(r, dict)]
    included = _included(data)
    out: list[dict[str, Any]] = []
    for attrs, resource in _jsonapi_rows(data):
        tech = next(
            (included.get(t, {}).get("name") for t in _related(resource, "technology")), None
        )
        if not tech:
            continue
        places = [
            {"city": loc.get("city"), "state": loc.get("state"), "country": loc.get("country")}
            for loc in (attrs.get("location_data") or [])
            if isinstance(loc, dict)
        ]
        out.append(
            {
                **attrs,
                "technology": str(tech),
                "locations": places,
                "seen_on_job_openings": len(_related(resource, "seen_on_job_openings")),
                "seen_on_subpages": len(_related(resource, "seen_on_subpages")),
            }
        )
    return out


def detections_of(data: Any) -> list[ImportedDetection]:
    """``techstack_detections.json`` records as detections."""
    out = []
    for r in _flatten_detections(data):
        if not isinstance(r, dict) or not str(r.get("technology") or "").strip():
            continue
        places = tuple(
            ", ".join(str(v) for v in (loc.get("city"), loc.get("state"), loc.get("country")) if v)
            if isinstance(loc, dict)
            else str(loc)
            for loc in (r.get("locations") or [])[:20]
        )
        out.append(
            ImportedDetection(
                technology=str(r["technology"])[:200],
                source_count=int(r.get("source_count") or 0),
                first_seen_at=str(r.get("first_seen_at") or "")[:10] or None,
                last_seen_at=str(r.get("last_seen_at") or "")[:10] or None,
                score=float(r["score"]) if r.get("score") is not None else None,
                seen_on_postings=int(r.get("seen_on_job_openings") or 0),
                seen_on_pages=int(r.get("seen_on_subpages") or 0),
                behind_firewall=bool(r["behind_firewall"]) if "behind_firewall" in r else None,
                locations=places,
            )
        )
    return out


def stack_of(data: Any) -> list[ImportedStackItem]:
    """``techstack_confirmed.json`` (business function -> category -> items) as stack rows."""
    out: list[ImportedStackItem] = []
    functions = None
    if isinstance(data, dict):
        for key in ("business_functions", "stack", "functions"):
            if isinstance(data.get(key), dict):
                functions = data[key]
                break
    if not isinstance(functions, dict):
        return out
    for function, categories in functions.items():
        if not isinstance(categories, dict):
            continue
        for category, items in categories.items():
            for item in items if isinstance(items, list) else []:
                name = str((item or {}).get("name") or "") if isinstance(item, dict) else str(item)
                if name.strip():
                    out.append(
                        ImportedStackItem(
                            business_function=str(function)[:200],
                            category=str(category)[:200],
                            technology=name[:200],
                            postings=int((item or {}).get("job_post_used_count") or 0)
                            if isinstance(item, dict)
                            else 0,
                        )
                    )
    return out


def teams_of(data: Any) -> list[ImportedTeam]:
    """``teams.json`` records as platform teams."""
    out = []
    for r in _records(data, "teams"):
        if not isinstance(r, dict) or not str(r.get("name") or "").strip():
            continue
        attrs: dict[str, Any] = r["attributes"] if isinstance(r.get("attributes"), dict) else {}
        techs = tuple(
            (str(t.get("name") or "")[:200], int(t.get("jobs_count") or 0))
            for t in (attrs.get("technology_list") or [])
            if isinstance(t, dict) and t.get("name")
        )
        places = tuple(
            (
                str(loc.get("name") or loc.get("location") or "")[:200],
                int(loc.get("jobs_count") or 0),
            )
            if isinstance(loc, dict)
            else (str(loc)[:200], None)
            for loc in (attrs.get("location_list") or [])
        )
        out.append(
            ImportedTeam(
                team=str(r["name"])[:300],
                function="; ".join(
                    str(f.get("name") if isinstance(f, dict) else f)
                    for f in (attrs.get("job_function_list") or [])[:10]
                    if (f.get("name") if isinstance(f, dict) else f)
                )
                or None,
                postings_count=int(attrs.get("jobs_count") or 0),
                first_activity=_day(attrs.get("first_activity")),
                last_activity=_day(attrs.get("last_activity")),
                lane_technologies=tuple(name for name, _ in techs if name.casefold() in LANE_WORDS),
                technologies=techs[:60],
                locations=places[:40],
            )
        )
    return out


LANE_WORDS = frozenset(
    {"salesforce", "mulesoft", "servicenow", "sap", "tableau", "veeva", "workday", "sap s/4hana"}
)


def _flatten_news(data: Any) -> list[dict[str, Any]]:
    """News event records as flat dicts: a list of events, or the JSON:API feed whose article
    (``most_relevant_source``) carries the address (the UCB pull, 2026-11-05)."""
    if not _is_jsonapi(data):
        return [r for r in _records(data, "events", "news") if isinstance(r, dict)]
    included = _included(data)
    out: list[dict[str, Any]] = []
    for attrs, resource in _jsonapi_rows(data):
        row = dict(attrs)
        for t in _related(resource, "most_relevant_source"):
            article = included.get(t, {})
            url = next(
                (
                    str(article[k])
                    for k in ("url", "link", "source_url", "article_url")
                    if article.get(k)
                ),
                "",
            )
            if url:
                row.setdefault("source_url", url)
            if article.get("published_at") and not row.get("effective_date"):
                row["effective_date"] = article["published_at"]
        if isinstance(row.get("location_data"), list) and not row.get("location"):
            first = next((x for x in row["location_data"] if isinstance(x, dict)), None)
            if first:
                row["location"] = ", ".join(
                    str(v) for v in (first.get("city"), first.get("country")) if v
                )
        out.append(row)
    return out


def news_of(data: Any) -> list[ImportedNewsEvent]:
    """``news_events.json`` records as news events."""
    out = []
    for r in _flatten_news(data):
        if not isinstance(r, dict) or not str(r.get("headline") or "").strip():
            continue
        related = r.get("related") or []
        out.append(
            ImportedNewsEvent(
                headline=str(r["headline"])[:1000],
                event_id=str(r.get("id") or "")[:200] or None,
                category=str(r.get("category") or "")[:60] or None,
                summary=str(r.get("summary") or "")[:4000] or None,
                article_sentence=str(r.get("article_sentence") or "")[:2000] or None,
                found_at=_day(r.get("found_at")),
                effective_date=_day(r.get("effective_date")),
                confidence=float(r["confidence"]) if r.get("confidence") is not None else None,
                source_url=str(r.get("source_url") or r.get("url") or "")[:2000] or None,
                location=str(r.get("location") or "")[:200] or None,
                related=tuple(str(x)[:200] for x in related[:20]),
            )
        )
    return out


def connections_of(data: Any, account_domain: str) -> list[ImportedConnection]:
    """``connections.json`` records as the account's connections (the other company)."""
    out = []
    own = account_domain.casefold()
    rows: list[dict[str, Any]] = []
    if _is_jsonapi(data):
        included = _included(data)
        for attrs, resource in _jsonapi_rows(data):
            row = dict(attrs)
            for side in ("company1", "company2"):
                company = next((included.get(t, {}) for t in _related(resource, side)), {})
                row[side] = str(company.get("domain") or "")
                row[f"{side}_name"] = company.get("company_name") or company.get("name")
            rows.append(row)
    else:
        rows = [r for r in _records(data, "connections") if isinstance(r, dict)]
    for r in rows:
        other = str(r.get("company1") or "")
        other_name = r.get("company1_name")
        if other.casefold() == own:
            other, other_name = str(r.get("company2") or ""), r.get("company2_name")
        if not other.strip():
            continue
        out.append(
            ImportedConnection(
                company_domain=other[:253],
                company_name=str(other_name or "")[:300] or None,
                category=str(r.get("category") or "other")[:60],
                source_category=str(r.get("source_category") or "")[:60] or None,
                first_seen_at=_day(r.get("first_seen_at")),
                last_seen_at=_day(r.get("last_seen_at")),
                source_url=str(r.get("source_url") or "")[:2000] or None,
            )
        )
    return out


def profiles_of(data: Any) -> list[ImportedProfile]:
    """``profiles_full.json`` records as profiles (the allow-listed fields only)."""
    out = []
    for r in _records(data, "profiles"):
        if not isinstance(r, dict):
            continue
        url = str(r.get("linkedin_url") or r.get("profile_url") or "")
        p = r.get("profile") if isinstance(r.get("profile"), dict) else {}
        if not url or not p:
            continue
        experiences = [
            e for e in (p.get("experiences") or []) if isinstance(e, dict) and e.get("company")
        ]
        location = p.get("location")
        out.append(
            ImportedProfile(
                profile_url=url[:2000],
                name=str(p.get("fullName") or r.get("name") or "")[:200] or None,
                current_title=str(p.get("currentTitle") or "")[:300] or None,
                headline=str(p.get("headline") or "")[:500] or None,
                employer=str(p.get("currentEmployer") or "")[:200] or None,
                employer_domain=str(p.get("currentEmployerDomain") or "")[:253] or None,
                prior_employers=tuple(
                    dict.fromkeys(str(e["company"])[:200] for e in experiences[1:])
                )[:50],
                employments=tuple(
                    (
                        str(e["company"])[:200],
                        str(e.get("title") or "")[:300] or None,
                        str(e.get("startDate") or "")[:10] or None,
                        str(e.get("endDate") or "")[:10] or None,
                    )
                    for e in experiences[:60]
                ),
                certifications=tuple(
                    str(c.get("name") or "")[:200]
                    for c in (p.get("certifications") or [])
                    if isinstance(c, dict) and c.get("name")
                )[:50],
                location=(
                    str(location.get("full") or "")[:200]
                    if isinstance(location, dict)
                    else str(location or "")[:200]
                )
                or None,
                own_description=str(p.get("summary") or "")[:8000] or None,
            )
        )
    return out


def posts_of(data: Any) -> tuple[list[ImportedPost], list[ImportedPost]]:
    """``posts_comments.json`` records as (posts, comments)."""
    posts: list[ImportedPost] = []
    comments: list[ImportedPost] = []
    for r in _records(data, "records", "rows"):
        if not isinstance(r, dict):
            continue
        url = str(r.get("linkedin_url") or "")
        if not url:
            continue
        for post in (
            ((r.get("posts") or {}).get("posts") or []) if isinstance(r.get("posts"), dict) else []
        ):
            if isinstance(post, dict) and post.get("postLink") and post.get("postText"):
                posts.append(
                    ImportedPost(
                        profile_url=url,
                        post_url=str(post["postLink"])[:2000],
                        excerpt=str(post["postText"])[:2000],
                        posted_at=_day(post.get("postedAt") or post.get("posted_at")),
                    )
                )
        comment_block: dict[str, Any] = r["comments"] if isinstance(r.get("comments"), dict) else {}
        for c in comment_block.get("comments") or []:
            if isinstance(c, dict) and c.get("postLink") and c.get("commentText"):
                comments.append(
                    ImportedPost(
                        profile_url=url,
                        post_url=str(c["postLink"])[:2000],
                        excerpt=str(c["commentText"])[:2000],
                        posted_at=_day(c.get("postedAt")),
                        post_excerpt=str(c.get("postText") or "")[:2000] or None,
                    )
                )
    return posts, comments


def leadership_of(data: Any, *, document_id: UUID) -> list[ImportedPerson]:
    """``leadership.json`` executives as people named on the leadership page (for review:
    the page names them, the workbook may hold their profile)."""
    from people.pipeline import person_key

    out = []
    page_url = str(data.get("url") or "") if isinstance(data, dict) else ""
    for r in _records(data, "executives", "executive_committee", "leadership", "people"):
        if not isinstance(r, dict) or not str(r.get("name") or "").strip():
            continue
        name = str(r["name"])[:200]
        title = str(r.get("title") or "")[:300] or None
        source_record_id = f"leadership:{name}|{title or ''}"[:300]
        out.append(
            ImportedPerson(
                person=Person(
                    person_key=person_key(source_record_id),
                    source_record_id=source_record_id,
                    origin="mention",
                    name=name,
                    title=title,
                    mention_url=str(r.get("page_url") or page_url or "")[:2000] or None,
                    provider_code="file_import",
                    raw_ref=f"document:{document_id}#leadership",
                    classification=PersonClassification(
                        status="review",
                        reason="named on the leadership page; no profile link in the pull",
                        rule_id="workspace_import",
                        level="other",
                    ),
                ),
                note="On the company's leadership page.",
            )
        )
    return out


def company_facts_of(data: Any, member: str) -> list[dict[str, Any]]:
    """The company record's fields (ticker, headquarters, employee count, subsidiaries) as
    facts of the account."""
    if not isinstance(data, dict):
        return []
    out = []
    pairs = (
        ("ticker", "company.profile.ticker"),
        ("hq", "company.profile.headquarters"),
        ("headquarters_country", "company.profile.headquarters_country"),
        ("employee_count", "company.profile.employee_count"),
        ("friendly_name", "company.profile.short_name"),
    )
    for key, attribute in pairs:
        value = data.get(key)
        if value not in (None, "", []):
            out.append({"attribute_key": attribute, "value": str(value)[:500], "member": member})
    domains = data.get("subsidiary_domains_reported")
    if isinstance(domains, list) and domains:
        out.append(
            {
                "attribute_key": "company.profile.subsidiary_domains",
                "value": ", ".join(str(d) for d in domains[:50])[:500],
                "member": member,
            }
        )
    return out


def _pick(record: Mapping[str, str], *names: str) -> str:
    for h, v in record.items():
        if any(n in h for n in names):
            return v
    return ""


def pull_calls_of(markdown: str) -> list[dict[str, str]]:
    """The call table of a pull log (``| Step | Tool | Parameters | Result | Output |``)."""
    rows = []
    header: list[str] | None = None
    for line in markdown.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            heads = [c.casefold() for c in cells]
            if any("tool" in c for c in heads) and any("step" in c for c in heads):
                header = heads
            continue
        if all(set(c) <= set("-: ") for c in cells) or len(cells) < 3:
            continue
        record = dict(zip(header, cells, strict=False))
        rows.append(
            {
                "step": _pick(record, "step"),
                "tool": _pick(record, "tool"),
                "parameters": _pick(record, "parameter"),
                "result": _pick(record, "result", "count"),
                "output": _pick(record, "output", "file"),
            }
        )
    return rows


def plan(
    members: Mapping[str, bytes],
    roles: Mapping[str, MemberRole],
    *,
    document_id: UUID,
    account_domain: str,
) -> RawPlan:
    """What the raw members become (pure)."""
    out = RawPlan()
    for member, raw in sorted(members.items()):
        role = roles.get(member)
        base = member.rsplit("/", 1)[-1].casefold()
        if role is MemberRole.CAPTURED_PAGE:
            meta, body = captured_page(raw.decode("utf-8", errors="replace"))
            url = meta.get("url", "")
            if url.startswith("http") and body:
                out.pages.append(
                    {
                        "member": member,
                        "url": url,
                        "title": meta.get("title") or member_title(member) or None,
                        "date": meta.get("date") or member_date(member),
                        "kind": page_kind(url, member),
                        "text": body,
                    }
                )
                out.records[member] += 1
            else:
                out.counts["pages_without_url"] += 1
            continue
        if role in (MemberRole.NOTE, MemberRole.RESTORE):
            text_body = raw.decode("utf-8", errors="replace")
            kind = (
                "restore"
                if role is MemberRole.RESTORE
                else next((k for needle, k in _NOTE_KINDS if needle in base), "note")
            )
            out.notes.append(
                {
                    "kind": kind,
                    "title": member.rsplit("/", 1)[-1][:300],
                    "text": text_body,
                    "audience": "client",
                    "member_path": member,
                }
            )
            out.records[member] += 1
            if kind == "pull_log":
                calls = pull_calls_of(text_body)
                out.calls.extend(calls)
                out.records[member] += len(calls)
            continue
        if role is not MemberRole.RAW_PULL or not base.endswith(".json"):
            continue
        data = _loads(raw)
        if data is None:
            out.counts["pulls_unreadable"] += 1
            continue
        n = 0
        if base.startswith("techstack_detections"):
            found_d = detections_of(data)
            out.detections.extend((member, d) for d in found_d)
            n = len(found_d)
        elif base.startswith("techstack_confirmed"):
            found_s = stack_of(data)
            out.stack.extend((member, d) for d in found_s)
            n = len(found_s)
        elif base.startswith("teams"):
            found_t = teams_of(data)
            out.teams.extend((member, d) for d in found_t)
            n = len(found_t)
        elif base.startswith("news_events"):
            found_n = news_of(data)
            out.news.extend((member, d) for d in found_n)
            n = len(found_n)
        elif base.startswith("connections"):
            found_c = connections_of(data, account_domain)
            out.connections.extend((member, d) for d in found_c)
            n = len(found_c)
        elif base.startswith("profiles_full"):
            found_p = profiles_of(data)
            out.profiles.extend((member, d) for d in found_p)
            n = len(found_p)
        elif base.startswith("posts_comments"):
            posts, comments = posts_of(data)
            out.posts.extend((member, d) for d in posts)
            out.comments.extend((member, d) for d in comments)
            n = len(posts) + len(comments)
        elif base.startswith("leadership"):
            found_l = leadership_of(data, document_id=document_id)
            out.people.extend((member, d) for d in found_l)
            n = len(found_l)
        elif base.startswith(("company_record", "org_resolve")):
            found_f = company_facts_of(data, member)
            out.company_facts.extend((member, d) for d in found_f)
            n = len(found_f)
        elif "searches" in base:
            found_q = searches_of(data)
            out.searches.extend((member, d) for d in found_q)
            n = len(found_q)
        else:
            out.counts["pulls_not_imported"] += 1
            continue
        out.records[member] += n
    return out


async def import_searches(
    s: Any, tenant_id: UUID, run_id: UUID, items: Sequence[Mapping[str, Any]], now: datetime
) -> int:
    """The research's web searches as calls of the file-import run in the tool-call ledger
    (tool ``p_search:web_search``, cost source ``prepulled``: a pre-pulled file consumed at no
    cost), the shape the Web searches table reads. One row per query; a query written before
    is not written again. Returns the rows written."""
    import hashlib

    from account_intelligence.connectors.ledger import record_prepulled_search

    written = 0
    for item in items:
        query = str(item.get("query") or "")
        if not query:
            continue
        args_hash = hashlib.sha256(f"web_search|{query.casefold()}".encode()).hexdigest()
        response = {
            "args": {"queries": [query], "objective": query},
            "record_count": int(item.get("pages") or 0),
            "pages": list(item.get("results") or ()),
            "purpose": str(item.get("purpose") or ""),
            "source": "workspace_import",
        }
        written += await record_prepulled_search(s, tenant_id, run_id, args_hash, response, now)
    return written


class _Clock:
    """The archive writer's clock: the import's own time."""

    def __init__(self, now: datetime) -> None:
        self._now = now if now.tzinfo else now.replace(tzinfo=UTC)

    def now(self) -> datetime:
        """The import's time."""
        return self._now


async def write(
    s: Any,
    *,
    txn: Any,
    tenant_id: UUID,
    account_id: UUID,
    run_id: UUID,
    document_id: UUID,
    raw_plan: RawPlan,
    payloads: Any | None,
    now: datetime,
    account_domain: str,
    account_name: str | None,
    visibility: str,
) -> Counter[str]:
    """Store a plan in the caller's transaction (the archive bodies go to the payload store
    first); per member, the rows the file held, imported and left. Returns the counts."""
    from account_intelligence.archive import LinkArchive
    from account_intelligence.archive_records import page_record
    from account_intelligence.grading.ports import RunInfo
    from account_intelligence.grading.repo import record_imported_fact

    counts: Counter[str] = Counter()
    imported: Counter[str] = Counter()
    source_ref = f"document:{document_id}"
    if raw_plan.pages and payloads is not None:
        archive = LinkArchive(txn, tenant_id, account_id, run_id, payloads, _Clock(now))
        entries = []
        for page in raw_plan.pages:
            captured = now
            record = page_record(
                page["url"],
                page["text"],
                title=page["title"],
                captured_at=captured,
                kind=page["kind"],
                published_date=page["date"],
            )
            entry = await archive.entry_for_record(
                page["url"], page["kind"], record, title=page["title"], source_ref=source_ref,
                archived_at=captured,
            )  # fmt: skip
            if entry is not None:
                entries.append(entry)
                imported[page["member"]] += 1
        counts["pages_written"] = await archive.record_in(s, entries)
    elif raw_plan.pages:
        counts["pages_without_store"] = len(raw_plan.pages)

    async def per_member(label: str, pairs: Sequence[tuple[str, Any]], writer: Any) -> None:
        """Write the items member by member so each member's imported count is its own."""
        by_member: dict[str, list[Any]] = {}
        for member, item in pairs:
            by_member.setdefault(member, []).append(item)
        for member, items in by_member.items():
            n = await writer(items)
            counts[f"{label}_written"] += n
            imported[member] += n

    await per_member(
        "detections",
        raw_plan.detections,
        lambda items: import_detections(s, tenant_id, account_id, run_id, items, source_ref),
    )
    await per_member(
        "stack",
        raw_plan.stack,
        lambda items: import_stack_items(s, tenant_id, account_id, run_id, items, source_ref),
    )
    await per_member(
        "teams",
        raw_plan.teams,
        lambda items: import_platform_teams(s, tenant_id, account_id, run_id, items, source_ref),
    )
    await per_member(
        "news",
        raw_plan.news,
        lambda items: import_news_events(s, tenant_id, account_id, run_id, items, source_ref),
    )
    await per_member(
        "connections",
        raw_plan.connections,
        lambda items: import_connections(s, tenant_id, account_id, run_id, items),
    )
    # a leadership-page person the run already holds (the workbook's row) is the same person:
    # folded, not added twice (the owner's rule on repeated people). The folded entry is kept as
    # one more source of that person, so an import of the same file again knows it even after
    # the person was erased (erasure nulls the name and keeps the keys): it is never re-added
    held_keys = {
        str(r.name).casefold(): str(r.person_key)
        for r in (
            await s.execute(
                text(
                    "SELECT name, person_key FROM people WHERE tenant_id = :t AND run_id = :r"
                    " AND name IS NOT NULL AND person_key IS NOT NULL"
                ),
                {"t": tenant_id, "r": run_id},
            )
        ).all()
    }
    seen = await known_sources(s, tenant_id, run_id)
    new_people = []
    folded: list[tuple[str, str, str]] = []
    for member, person in raw_plan.people:
        if person.person.source_record_id in seen:
            continue  # folded or stored by an earlier import of this file
        held = held_keys.get((person.person.name or "").casefold())
        if held is None:
            new_people.append((member, person))
        else:
            folded.append((held, person.person.source_record_id, person.person.origin))
    await record_folded_sources(s, tenant_id, account_id, run_id, folded)
    counts["leadership_merged"] = len(raw_plan.people) - len(new_people)
    await per_member(
        "leadership",
        new_people,
        lambda items: import_people(s, tenant_id, account_id, run_id, items),
    )
    await per_member(
        "profiles",
        raw_plan.profiles,
        lambda items: import_profiles(s, tenant_id, account_id, run_id, items),
    )

    async def posts_writer(items: Sequence[ImportedPost]) -> int:
        """Write posts; the number written."""
        n_posts, _ = await import_posts(s, tenant_id, account_id, run_id, items, ())
        return n_posts

    async def comments_writer(items: Sequence[ImportedPost]) -> int:
        """Write comments; the number written."""
        _, n_comments = await import_posts(s, tenant_id, account_id, run_id, (), items)
        return n_comments

    await per_member("posts", raw_plan.posts, posts_writer)
    await per_member("comments", raw_plan.comments, comments_writer)

    info = RunInfo(tenant_id, account_id, run_id, now, account_domain, account_name)
    for member, f in raw_plan.company_facts:
        _, created = await record_imported_fact(
            s,
            run=info,
            attribute_key=str(f["attribute_key"]),
            value=str(f["value"]),
            quote=str(f["value"])[:600],
            document_id=document_id,
            location={"member": member},
            grade="confirmed",
            visibility=visibility,
            flags=("workspace_import",),
            config_hash="workspace-raw-import-v1",
        )
        counts["company_facts_written"] += int(created)
        imported[member] += int(created)
    await per_member(
        "searches",
        raw_plan.searches,
        lambda items: import_searches(s, tenant_id, run_id, items, now),
    )
    counts["notes_written"] = await repo.import_notes(
        s, account_id, run_id, document_id, raw_plan.notes
    )
    for note in raw_plan.notes:
        imported[str(note["member_path"])] += 1
    counts["calls_written"] = await repo.import_pull_calls(
        s, account_id, run_id, document_id, raw_plan.calls
    )
    for note in raw_plan.notes:
        if note["kind"] == "pull_log":
            imported[str(note["member_path"])] += counts["calls_written"]
    # the reconciliation per member: records in the file = imported + merged (a record the run
    # already held, folded by the writers' conflict rules) + skipped
    for member, total in raw_plan.records.items():
        done = min(imported.get(member, 0), total)
        await repo.set_member_import_counts(s, document_id, member, done, total - done, 0)
    counts.update(raw_plan.counts)
    return counts
