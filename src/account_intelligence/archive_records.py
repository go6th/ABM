"""The structured records the link archive keeps (``*-archive/1``), one schema per kind of link.

Every address a research cites is archived as a small JSON record the website shows like a web
archive page: a news article or press release (``news-archive/1``), any other page
(``page-archive/1``), a person's profile (``profile-archive/1``), a technology detection
(``detection-archive/1``); a job posting is ``posting-archive/1`` (``jobs.snapshot`` builds it
with the helpers here). Pure functions: the writers store the bytes and record the entry.

Client-visible, so a record names no data tool, provider, ledger reference or credit figure: it is
built from the fields the page shows, every free-text field passes a guard (``Guard``: the text to
show and whether it must be withheld) and a field that fails is blank and listed under
``withheld``, never the whole copy.

Identity: a copy is content-addressed over what the page SAID (``identity_bytes``: not the capture
time), so a later research that sees the same page unchanged writes nothing new and changed
content writes a new copy beside the old one (the archive is insert-only; the newest copy wins).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from account_intelligence import archive_facts as AF
from account_intelligence.archive_clean import check_clean, clean_page
from account_intelligence.safety.client_output import scan_text
from account_intelligence.urls import strip_secrets

SOURCE = "6th agents"
PAGE = "page-archive/1"
NEWS = "news-archive/1"
PROFILE = "profile-archive/1"
DETECTION = "detection-archive/1"
# version 2 of a page or news copy: the cleaned article, its tables, typed facts and provenance
NEWS2 = "news-archive/2"
PAGE2 = "page-archive/2"
# fields that say when we looked, not what the page said: left out of the identity
NOT_IDENTITY = frozenset({"captured_at", "collected_at", "first_seen", "last_seen", "withheld"})
# the longest body text a record keeps (a page read in full can be very long)
MAX_TEXT = 200_000

Guard = Callable[[str], tuple[str, bool]]
"""The text to show and whether it must be withheld (the shape of ``scrub_client_text``)."""


# the page guard of the running application (``agents.naming.archive_guard``), registered when
# that module is imported: the default every writer uses, so there is ONE write-time guard path
# (review 2026-10-29, F9). Until it is registered only the platform denylist applies.
_page_guard: list[Guard] = []


def use_default_guard(guard: Guard) -> None:
    """Make ``guard`` the default of every writer (the application's page guard)."""
    _page_guard[:] = [guard]


def default_guard(text: str) -> tuple[str, bool]:
    """The guard a writer uses when it is given none: the application's page guard when one is
    registered (it scrubs tool names and checks provider names, domains and person names too),
    else the platform denylist (``safety.client_output``): withhold text that names a broker or a
    credit figure."""
    if _page_guard:
        shown, refused = _page_guard[0](text)
        return shown, refused
    return text, bool(scan_text(text))


_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)")
# a run of digits that is a date, not a number to ring: 2026-10-30, 30.10.2026, 30/10/26
_DATE = re.compile(r"^(?:\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}[-./]\d{1,2}[-./]\d{2,4})$")


def _phone(match: re.Match[str]) -> str:
    return match.group(0) if _DATE.match(match.group(0)) else "[redacted]"


def redact_contact(text: str) -> str:
    """The text without email addresses and phone numbers (the same rule as the posting
    snapshot's ``strip_pii``, except that a date is not mistaken for a number): every archived
    page and news copy, and a legacy plain-text copy of a posting, is stored and served through
    it. Contact details are raw payload only (``docs/data-handling-people-data.md``): nobody in
    client output, however public the page that printed them."""
    return _PHONE.sub(_phone, _EMAIL.sub("[redacted]", text))


def publisher_of(url: str) -> str:
    """The site an address belongs to (its host without ``www.``), the publisher a page shows."""
    host = (urlsplit(url.strip()).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def day_of(value: object) -> str | None:
    """A date as ``YYYY-MM-DD`` (a date, a datetime or an ISO text), else None."""
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return str(iso())[:10]
    if isinstance(value, str) and len(value.strip()) >= 10:
        return value.strip()[:10]
    return None


def guarded(
    record: dict[str, Any], fields: Iterable[str], guard: Guard, lists: Iterable[str] = ()
) -> dict[str, Any]:
    """The record with each free-text field the guard refuses blanked and named under
    ``withheld`` (``lists``: fields that are lists of texts, each text checked on its own)."""
    withheld: list[str] = []
    for field in fields:
        shown, refused = guard(str(record.get(field) or ""))
        if refused:
            record[field] = ""
            withheld.append(field)
        else:
            record[field] = shown
    for field in lists:
        kept: list[str] = []
        for text in record.get(field) or []:
            shown, refused = guard(str(text))
            if refused:
                withheld.append(field)
            else:
                kept.append(shown)
        record[field] = kept
    if withheld:
        record["withheld"] = sorted(set(withheld))
    return record


# the record fields the archive index shows beside a row (``link_archive.meta``) and the most
# characters each keeps; a list field (event types, people, lanes) is joined with "; "
INDEX_FIELDS = ("status_at_capture", "location", "requisition")
WEB_INDEX_FIELDS = {
    "publisher": 60,
    "published_date": 10,
    "source_type": 30,
    "fact_grade": 12,
    "event_types": 80,
    "people": 100,
    "lanes": 80,
}
# the table constraint is 2048 bytes of jsonb text; stay clear of it for non-ASCII text
META_BUDGET = 1800


def index_meta(record: Mapping[str, Any]) -> dict[str, str] | None:
    """The few fields the archive index shows for a copy (status when captured, location,
    requisition, the record's schema; for a page or news copy also publisher, published date,
    source type, grade, event types, people named and lanes), each a short text, taken from the
    record when it is stored so the index never opens the body (security review 2026-10-29, F12).
    None when the record has none of them. The whole stays under ``META_BUDGET`` bytes: optional
    fields are dropped from the end of the list until it does."""
    meta: dict[str, str] = {}
    for key in (*INDEX_FIELDS, "schema"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            meta[key] = " ".join(value.split())[:200]
    derived = {
        "event_types": [e.get("event_type", "") for e in record.get("events") or []],
        "people": [p.get("name", "") for p in record.get("people") or []],
        "lanes": list(record.get("lanes") or []),
    }
    for key, limit in WEB_INDEX_FIELDS.items():
        value = derived.get(key, record.get(key))
        if isinstance(value, list):
            value = "; ".join(dict.fromkeys(str(v) for v in value if v))
        if isinstance(value, str) and value.strip() and value not in (AF.NOT_STATED, AF.NOT_FOUND):
            meta[key] = " ".join(value.split())[:limit]
    for key in reversed(list(WEB_INDEX_FIELDS)):
        if len(json.dumps(meta, ensure_ascii=False).encode()) <= META_BUDGET:
            break
        meta.pop(key, None)
    return meta or None


def snapshot_bytes(record: Mapping[str, Any]) -> bytes:
    """The stored body: the record as stable JSON."""
    return json.dumps(record, sort_keys=True, ensure_ascii=False).encode()


def identity_bytes(record: Mapping[str, Any]) -> bytes:
    """What the page said, without when we looked: the content the archive key is made of."""
    said = {k: v for k, v in record.items() if k not in NOT_IDENTITY}
    return json.dumps(said, sort_keys=True, ensure_ascii=False).encode()


def _cut(text: str) -> str:
    return text[:MAX_TEXT]


def page_record(
    url: str,
    text: str,
    *,
    title: str | None,
    captured_at: datetime,
    kind: str = "page",
    published_date: str | None = None,
    guard: Guard = default_guard,
) -> dict[str, Any]:
    """A page (or any item with a title and a text): ``page-archive/1``."""
    record: dict[str, Any] = {
        "schema": PAGE,
        "url": strip_secrets(url),
        "kind": kind,
        "title": redact_contact(title or ""),
        "publisher": publisher_of(url),
        "published_date": day_of(published_date),
        "text": redact_contact(_cut(text)),
        "captured_at": captured_at.isoformat(),
        "source": SOURCE,
    }
    return guarded(record, ("title", "text"), guard)


def news_record(
    url: str,
    *,
    title: str | None,
    summary: str | None,
    article_text: str,
    category: str | None,
    published_date: str | None,
    captured_at: datetime,
    guard: Guard = default_guard,
) -> dict[str, Any]:
    """A news article or press release: ``news-archive/1``."""
    record: dict[str, Any] = {
        "schema": NEWS,
        "url": strip_secrets(url),
        "title": redact_contact(title or ""),
        "publisher": publisher_of(url),
        "published_date": day_of(published_date),
        "category": (category or "").replace("_", " "),
        "summary": redact_contact(summary or ""),
        "article_text": redact_contact(_cut(article_text)),
        "captured_at": captured_at.isoformat(),
        "source": SOURCE,
    }
    return guarded(record, ("title", "category", "summary", "article_text"), guard)


def profile_record(
    url: str,
    *,
    name: str,
    job_title: str | None,
    headline: str | None,
    location: str | None,
    captured_at: datetime,
    guard: Guard = default_guard,
) -> dict[str, Any]:
    """A person's profile page: ``profile-archive/1`` (what the source returned)."""
    lines = [name, job_title, headline, location]
    record: dict[str, Any] = {
        "schema": PROFILE,
        "url": url,
        "name": name,
        "job_title": job_title or "",
        "headline": headline or "",
        "location": location or "",
        "text": "\n".join(x for x in lines if x),
        "captured_at": captured_at.isoformat(),
        "source": SOURCE,
    }
    return guarded(record, ("name", "job_title", "headline", "location", "text"), guard)


def detection_record(
    url: str,
    *,
    technology: str,
    first_seen: str | None,
    last_seen: str | None,
    sources: int,
    captured_at: datetime,
    guard: Guard = default_guard,
) -> dict[str, Any]:
    """A technology detection and the page that evidenced it: ``detection-archive/1``."""
    record: dict[str, Any] = {
        "schema": DETECTION,
        "url": url,
        "technology": technology,
        "first_seen": day_of(first_seen),
        "last_seen": day_of(last_seen),
        "sources": sources,
        "text": f"{technology}\nFirst seen {day_of(first_seen) or 'not given'}\n"
        f"Last seen {day_of(last_seen) or 'not given'}\nSources {sources}",
        "captured_at": captured_at.isoformat(),
        "source": SOURCE,
    }
    return guarded(record, ("technology", "text"), guard)


def _guard_tree(value: Any, guard: Guard, held: set[str], top: str) -> Any:
    """The value with every text the guard refuses blanked (a table cell keeps its place) and the
    top-level field named in ``held``."""
    if isinstance(value, str):
        shown, refused = guard(value)
        if refused:
            held.add(top)
            return ""
        return shown
    if isinstance(value, list):
        return [_guard_tree(v, guard, held, top) for v in value]
    if isinstance(value, dict):
        return {k: _guard_tree(v, guard, held, top) for k, v in value.items()}
    return value


def _redact_tree(value: Any) -> Any:
    if isinstance(value, str):
        return redact_contact(value)
    if isinstance(value, list):
        return [_redact_tree(v) for v in value]
    if isinstance(value, dict):
        return {k: _redact_tree(v) for k, v in value.items()}
    return value


def web_record(
    url: str,
    raw: str,
    *,
    schema: str,
    title: str | None,
    captured_at: datetime,
    kind: str = "page",
    published_date: str | None = None,
    category: str | None = None,
    summary: str | None = None,
    author: str | None = None,
    account: AF.Account | None = None,
    guard: Guard = default_guard,
) -> dict[str, Any]:
    """A page or news copy as it is kept (``page-archive/2`` or ``news-archive/2``): the article
    cleaned of the site's chrome and repeats, its tables rebuilt, the source classified and graded,
    typed facts (events, people, organisations, figures, quotes), commentary kept apart from the
    facts, and a validation of the result. ``raw`` is the page as captured; it is not kept here
    (the pull's own payload keeps it for audit). Every free text passes contact redaction and the
    output guard; a text the guard refuses is blank and its field is named under ``withheld``."""
    account = account or AF.Account()
    shown_title = " ".join((title or "").split())
    cleaned = clean_page(raw[: MAX_TEXT * 2], title=shown_title, host=publisher_of(url))
    cls = AF.classify(url, kind=kind, title=shown_title, raw=raw, account=account)
    collected = captured_at.isoformat()
    published = day_of(published_date) or AF.published_from_text(raw) or None
    if published is None and cleaned.byline:
        dates = AF.find_dates(cleaned.byline)
        published = dates[0][0] if dates else None
    if published == collected[:10] and not any(d[0] == published for d in AF.find_dates(raw)):
        published = None  # the collection date is not the publication date
    body = cleaned.body[:MAX_TEXT]
    events, people = AF.people_changes(body, announced=published)
    events += [e for e in AF.other_events(shown_title, body, announced=published)]
    orgs = AF.organizations(shown_title, body, raw, publisher=publisher_of(url), account=account)
    subject = next((o["name"] for o in orgs if o["role_in_story"] == "Subject"), AF.NOT_STATED)
    for person in people:
        person["organization"] = subject
    for event in events:
        event["confidence"] = cls.fact_grade
    tags, lanes = AF.tags_and_lanes(shown_title, body)
    author_type = (
        "AI"
        if cls.is_ai_generated
        else ("Journalist" if cls.source_type == "Trade press" else "Analyst")
    )
    record: dict[str, Any] = {
        "schema": schema,
        "url": strip_secrets(url),
        "canonical_url": strip_secrets(url),
        "title": shown_title,
        "publisher": publisher_of(url),
        "author": author or AF.NOT_STATED,
        "published_date": published or AF.NOT_STATED,
        "collected_at": collected,
        "language": cleaned.language,
        "source_type": cls.source_type,
        "is_primary": cls.is_primary,
        "is_ai_generated": cls.is_ai_generated,
        "derived_from": cls.derived_from,
        "fact_grade": cls.fact_grade,
        "summary": " ".join((summary or "").split()) or AF.lead_summary(body),
        "body_markdown": body,
        "tables": cleaned.tables,
        "events": events,
        "people": people,
        "organizations": orgs,
        "figures": AF.figures(body),
        "quotes": AF.quotes(body),
        "analysis": [{"author_type": author_type, "text": a["text"]} for a in cleaned.analysis],
        "disclaimer": cleaned.disclaimer,
        "tags": tags,
        "lanes": lanes,
        "duplicate_of": None,
        "corroborates": [],
        "quality": {
            "boilerplate_removed": cleaned.boilerplate_removed,
            "duplicates_removed": cleaned.duplicates_removed,
            "tables_repaired": cleaned.tables_repaired,
            "word_count": cleaned.words,
        },
        "source": SOURCE,
    }
    if schema == NEWS2:
        record["category"] = (category or "").replace("_", " ")
    else:
        record["kind"] = kind
    record = _redact_tree(record)
    held: set[str] = set()
    # the article is guarded paragraph by paragraph: a refused paragraph goes, the rest stays
    kept: list[str] = []
    for paragraph in record["body_markdown"].split("\n\n"):
        shown, refused = guard(paragraph)
        if refused:
            held.add("body_markdown")
        elif shown.strip():
            kept.append(shown)
    record["body_markdown"] = "\n\n".join(kept)
    for field in (
        "title",
        "summary",
        "disclaimer",
        "tables",
        "events",
        "people",
        "organizations",
        "figures",
        "quotes",
        "analysis",
        "derived_from",
        "author",
    ):
        record[field] = _guard_tree(record[field], guard, held, field)
    if held:
        record["withheld"] = sorted(held)
    record["content_hash"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                [record["title"], record["body_markdown"], record["tables"]], ensure_ascii=False
            ).encode()
        ).hexdigest()
    )
    flags = validate_web_record(record)
    record["quality"]["validation"] = "flagged" if flags else "passed"
    if flags:
        record["quality"]["flags"] = flags
    return record


def validate_web_record(record: Mapping[str, Any]) -> list[str]:
    """What is wrong with a version 2 record (empty when it passes the checks of step 7): chrome,
    repeats or markdown artifacts in the text, a ragged table, a missing classification, a
    publication date that is only the collection date, an event without a type or date, a
    person's name that is not one."""
    flags = check_clean(str(record.get("body_markdown") or ""), list(record.get("tables") or []))
    for key in ("source_type", "fact_grade"):
        if not record.get(key):
            flags.append(f"{key} not set")
    if not isinstance(record.get("is_ai_generated"), bool):
        flags.append("is_ai_generated not set")
    if record.get("is_ai_generated") and record.get("fact_grade") == "Confirmed":
        flags.append("an AI summary is graded Confirmed")
    if record.get("published_date") == str(record.get("collected_at", ""))[:10]:
        flags.append("published date equals the collection date")
    for event in record.get("events") or []:
        dated = event.get("effective_date") or event.get("announced_date")
        if not event.get("event_type") or not dated:
            flags.append("an event has no type or date")
            break
    for person in record.get("people") or []:
        if AF.person_name(str(person.get("name", ""))) is None:
            flags.append(f"not a person's name: {person.get('name')}")
            break
    for text in [record.get("title"), record.get("summary"), record.get("disclaimer")]:
        if isinstance(text, str) and any(a in text for a in ("**", "\\#", "[](")):
            flags.append("markdown artifact in a heading text")
            break
    return flags
