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

import json
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from account_intelligence.safety.client_output import scan_text
from account_intelligence.urls import strip_secrets

SOURCE = "6th agents"
PAGE = "page-archive/1"
NEWS = "news-archive/1"
PROFILE = "profile-archive/1"
DETECTION = "detection-archive/1"
# fields that say when we looked, not what the page said: left out of the identity
NOT_IDENTITY = frozenset({"captured_at", "first_seen", "last_seen", "withheld"})
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


# the record fields the archive index shows beside a row (``link_archive.meta``)
INDEX_FIELDS = ("status_at_capture", "location", "requisition")


def index_meta(record: Mapping[str, Any]) -> dict[str, str] | None:
    """The few fields the archive index shows for a copy (status when captured, location,
    requisition, the record's schema), each a short text, taken from the record when it is
    stored so the index never opens the body (security review 2026-10-29, F12). None when the
    record has none of them."""
    meta: dict[str, str] = {}
    for key in (*INDEX_FIELDS, "schema"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            meta[key] = " ".join(value.split())[:200]
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
