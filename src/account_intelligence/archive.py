"""The link archive: every address a research stores, kept with the record it came from.

A People List row has a profile link, a Job Postings row a posting link, a fact its source page;
those links go stale (a posting closes, a profile is renamed, a page moves). The research already
stores what it read: Phase 1 stores one payload per source item (``agents.production.payloads``,
a JSON object with the item's ``url`` and ``text``), the job pipeline stores every posting's text,
the connector ledger keeps every raw provider body. The archive is the index over those copies:
one row per captured copy of an address (``link_archive``, insert-only) with the original URL,
its normalised form and hash (the lookup key, proven by the database), the kind of link, the
neutral payload key of the copy, its format (a JSON item or plain text), the record locator
inside a multi-record body, a title, when it was captured and the ledger reference of the raw
body it came from (traceability only; a provider body is never served, it would name the
provider). The newest copy of an address is the one a row opens.

Writers (the module that persists each row kind records its links in the same transaction):
Phase 1 items as they are stored, people with a profile link, postings with stored text, and
technology detections with an evidence page. Readers: ``lookup`` gives the sheets the archived
copy behind each link of a page of rows; ``archived_copy`` serves one copy; ``profile_links``
gives the people pipeline the LinkedIn profile pages the research's own searches returned, so a
person named in a source or returned without a link can be matched to a profile the research
already holds (no paid call).

Profiles (kind ``profile``) are INTERNAL inputs: they let the people pipeline match a named
executive to a profile page the research already holds, and nothing else. No client route lists
or serves them and no sheet links to one (``CLIENT_KINDS``): a profile copy holds the name, title,
headline and location of a person, and the People List omits excluded, out-of-scope and review
people on purpose (security review 2026-10-29, F1; decision recorded in the done note). The
erasure of a person deletes their profile index rows (``erase_urls``).

GUARANTEE(test_a_stored_link_is_archived_and_served_back)
GUARANTEE(test_the_newest_copy_of_an_address_wins)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text

from account_intelligence import archive_records as AR
from account_intelligence.urls import normalize_url, strip_secrets, url_hash

KINDS = frozenset({"profile", "posting", "page", "feed_item", "detection"})
# what a client route may list or serve: every kind but the internal profile copies
CLIENT_KINDS = KINDS - {"profile"}
FORMATS = frozenset({"json", "text"})
MAX_TITLE = 300
# the most text one archived copy answers in the JSON view (a page read in full can be 200,000
# characters; the download gives all of it)
MAX_COPY_TEXT = 100_000
# old rows (written before migration 0092) hold no index fields: a page of the index reads at
# most this many of their bodies, the rest list with those fields blank
LEGACY_BODY_READS = 50
# the most archived profile pages the people pipeline reads back for one account
MAX_PROFILE_LINKS = 4000
# a person's own page on the profile host (``people.urls`` decides canonical identity; this only
# names the kind of a stored link)
_PROFILE_PATH = re.compile(r"^/(?:mwlite/)?(?:in|pub)/[^/]+", re.IGNORECASE)
_PROFILE_HOST = "linkedin.com"


class PayloadStore(Protocol):
    """The platform payload store (``platform.ports.PayloadStore``)."""

    async def put(self, key: str, body: bytes) -> str:
        """Store a body under a neutral key; the key."""
        ...

    async def get(self, key: str) -> bytes:
        """The stored body (an error when missing)."""
        ...


@dataclass(frozen=True)
class ArchiveEntry:
    """One captured copy of an address, as a writer records it."""

    url: str
    kind: str
    payload_ref: str
    archived_at: datetime
    format: str = "json"
    locator: str | None = None
    title: str | None = None
    source_ref: str | None = None
    # the index fields of the copy (``archive_records.index_meta``), stored beside the row
    meta: dict[str, Any] | None = None


@dataclass(frozen=True)
class ArchivedLink:
    """The newest copy of one address, as the sheets and the people pipeline read it."""

    url: str
    url_normalized: str
    kind: str
    payload_ref: str
    format: str
    archived_at: datetime
    title: str | None = None
    locator: str | None = None
    meta: dict[str, Any] | None = None


@dataclass(frozen=True)
class ArchivedCopy:
    """One archived copy as the client sees it: the address, when it was captured, its text."""

    url: str
    kind: str
    archived_at: datetime
    title: str | None
    text: str
    # the structured record of a copy that has one (a posting's ``posting-archive/1`` snapshot);
    # None for a plain text or item copy
    record: dict[str, Any] | None = None
    # true when ``text`` was cut to ``MAX_COPY_TEXT`` (the download gives all of it)
    truncated: bool = False


@dataclass(frozen=True)
class ArchiveListItem:
    """One address in the account's archive index: its newest copy, with the few fields of a
    posting's record the index shows."""

    url: str
    kind: str
    archived_at: datetime
    title: str | None
    status_at_capture: str | None = None
    location: str | None = None
    requisition: str | None = None
    # the structured record's schema (``news-archive/1``): the index names the kind of page by it
    record_schema: str | None = None


def normalized(url: str | None) -> tuple[str, str] | None:
    """``(url_normalized, url_hash)`` of an http(s) address, None for anything else. The address
    is keyed without credentials and secret query parameters (``urls.strip_secrets``), the form
    the archive stores and shows."""
    if not url:
        return None
    try:
        norm = normalize_url(strip_secrets(url))
    except ValueError:
        return None
    return norm, url_hash(norm)


def hash_candidates(url: str | None) -> list[str]:
    """The ``url_hash`` values a copy of ``url`` may be stored under: the key of the address
    without its secrets and, for a copy written before that rule, the key of the address as
    given."""
    out: list[str] = []
    if not url:
        return out
    key = normalized(url)
    if key is not None:
        out.append(key[1])
    try:
        legacy = url_hash(normalize_url(url))
    except ValueError:
        return out
    if legacy not in out:
        out.append(legacy)
    return out


def on_profile_host(url: str) -> bool:
    """True for an address on the profile host (``linkedin.com`` and its subdomains)."""
    host = (urlsplit(url.strip()).hostname or "").lower().rstrip(".")
    return host == _PROFILE_HOST or host.endswith("." + _PROFILE_HOST)


def link_kind(url: str, default: str = "page") -> str:
    """``profile`` for a person's own page on the profile host, else ``default``."""
    if on_profile_host(url) and _PROFILE_PATH.match(urlsplit(url.strip()).path or ""):
        return "profile"
    return default if default in KINDS else "page"


def title_of(body_text: str | None) -> str | None:
    """The first non-empty line of a stored text (a search result's title), cut to the column."""
    for line in (body_text or "").splitlines():
        line = " ".join(line.split())
        if line:
            return line[:MAX_TITLE]
    return None


def archive_key(tenant_id: UUID, account_id: UUID, body: bytes) -> str:
    """A neutral, content-addressed key for a body the archive writes itself."""
    digest = hashlib.sha256(body).hexdigest()
    return f"tenants/{tenant_id}/accounts/{account_id}/archive/{digest}.json.gz"


def archive_body(
    url: str,
    body_text: str,
    *,
    title: str | None,
    captured_at: datetime,
    kind: str,
    source_ref: str | None = None,
) -> bytes:
    """The JSON body the archive stores for a record it copies itself (a profile, a detection)."""
    return json.dumps(
        {
            "url": url,
            "text": body_text,
            "title": title,
            "kind": kind,
            "captured_at": captured_at.isoformat(),
            "source_ref": source_ref,
        },
        sort_keys=True,
    ).encode()


def _cut(value: str, limit: int | None) -> tuple[str, bool]:
    """``value`` cut to ``limit`` characters (None: whole) and whether it was cut."""
    if limit is None or len(value) <= limit:
        return value, False
    return value[:limit], True


def copy_from(link: ArchivedLink, body: bytes, *, max_text: int | None = None) -> ArchivedCopy:
    """The archived copy read out of its stored body: a JSON item's ``text``, a structured record
    (its ``description`` is the text, the whole object is ``record``), or plain text (the old
    posting copies). ``max_text`` cuts the text (``truncated`` says so); a plain text copy is
    never decoded past it."""
    if link.format == "text":
        raw = body if max_text is None else body[: max_text * 4 + 4]
        shown, cut = _cut(AR.redact_contact(raw.decode("utf-8", "replace")), max_text)
        return ArchivedCopy(
            link.url,
            link.kind,
            link.archived_at,
            link.title,
            shown,
            truncated=cut or len(raw) < len(body),
        )
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("archived body is not an object")
    # a copy stored before contact details and secret addresses were removed at write time is
    # served through the same rule (the stored body is never changed)
    data = _clean_body(data)
    record = data if _is_record(data) else None
    field = next((f for f in _TEXT_FIELDS if isinstance(data.get(f), str)), None)
    if field is None or (record is None and field != "text"):
        raise ValueError("archived body has no text")
    data_title = data.get("title") if isinstance(data.get("title"), str) else None
    shown, cut = _cut(data[field], max_text)
    return ArchivedCopy(
        link.url, link.kind, link.archived_at, link.title or data_title, shown, record, cut
    )


# the free-text fields of a stored body that pass the contact-detail rule when it is served
_CONTACT_FIELDS = ("text", "article_text", "summary", "description", "title", "headline")


def _clean_body(data: dict[str, Any]) -> dict[str, Any]:
    """The body with email addresses and phone numbers removed from its free texts and its own
    address stripped of credentials and secret query parameters."""
    out = dict(data)
    for field in _CONTACT_FIELDS:
        value = out.get(field)
        if isinstance(value, str):
            out[field] = AR.redact_contact(value)
    if isinstance(out.get("url"), str):
        out["url"] = strip_secrets(out["url"])
    return out


# the field of a structured record that holds its text (posting description, article text, page
# text); an older plain body keeps its text under ``text``
_TEXT_FIELDS = ("description", "article_text", "text")


def _is_record(data: dict[str, Any]) -> bool:
    """A structured record: it names its schema (``posting-archive/1``)."""
    schema = data.get("schema")
    return isinstance(schema, str) and "/" in schema


def _rows(entries: Iterable[ArchiveEntry]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for e in entries:
        key = normalized(e.url)
        if key is None or e.kind not in KINDS or e.format not in FORMATS or not e.payload_ref:
            continue
        if (key[1], e.payload_ref) in seen:
            continue
        seen.add((key[1], e.payload_ref))
        when = e.archived_at if e.archived_at.tzinfo else e.archived_at.replace(tzinfo=UTC)
        out.append(
            {
                "url": strip_secrets(e.url.strip())[:2048],
                "norm": key[0],
                "hash": key[1],
                "kind": e.kind,
                "ref": e.payload_ref,
                "fmt": e.format,
                "loc": e.locator,
                "title": (e.title or None) and e.title[:MAX_TITLE],
                "src": e.source_ref,
                "at": when,
                "meta": json.dumps(e.meta, sort_keys=True) if e.meta else None,
            }
        )
    return out


# the row is owned like its account (private workspaces): the writer is usually a research
# worker in system scope, so the account's owner is named on every insert. A copy is
# content-addressed: a page that returns to earlier content maps to the OLD copy, which then moves
# forward in time (the only column that may change, migration 0092) so the newest capture wins
# (review 2026-10-29, F13).
_INSERT = text(
    "INSERT INTO link_archive (tenant_id, owner_user_id, account_id, run_id, url,"
    " url_normalized, url_hash, kind, payload_ref, format, locator, title, source_ref,"
    " archived_at, meta) VALUES (:t, coalesce((SELECT a.owner_user_id FROM accounts a"
    " WHERE a.tenant_id = :t AND a.id = :a), app_user()), :a, :r, :url, :norm, :hash, :kind,"
    " :ref, :fmt, :loc, :title, :src, :at, CAST(:meta AS jsonb))"
    " ON CONFLICT (tenant_id, account_id, url_hash, payload_ref) DO UPDATE"
    " SET archived_at = EXCLUDED.archived_at"
    " WHERE link_archive.archived_at < EXCLUDED.archived_at"
)


async def record(
    session: Any,
    tenant_id: UUID,
    account_id: UUID,
    run_id: UUID | None,
    entries: Iterable[ArchiveEntry],
) -> int:
    """Record the copies in the caller's transaction (a copy already recorded is skipped);
    the number of rows written."""
    rows = _rows(entries)
    for row in rows:
        await session.execute(_INSERT, {"t": tenant_id, "a": account_id, "r": run_id, **row})
    return len(rows)


def _link(row: Any) -> ArchivedLink:
    return ArchivedLink(
        url=strip_secrets(row.url),
        url_normalized=row.url_normalized,
        kind=row.kind,
        payload_ref=row.payload_ref,
        format=row.format,
        archived_at=row.archived_at,
        title=row.title,
        locator=row.locator,
        meta=row.meta if isinstance(row.meta, dict) else None,
    )


async def lookup(
    session: Any,
    account_id: UUID,
    urls: Sequence[str],
    *,
    kinds: frozenset[str] | None = None,
) -> dict[str, ArchivedLink]:
    """The archived copy of each address, keyed by the address as given (addresses that are not
    http(s), or have no copy, are absent): the newest structured record (a posting, a feed item,
    a detection) and only when there is none the newest page copy, so the richer record opens.
    ``kinds`` keeps only copies of those kinds (a client route passes ``CLIENT_KINDS``: a
    profile copy is not served)."""
    keys: dict[str, list[str]] = {}
    for url in urls:
        candidates = hash_candidates(url)
        if candidates:
            keys.setdefault(url, candidates)
    if not keys:
        return {}
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT ON (url_hash) url, url_normalized, url_hash, kind, payload_ref,"
                " format, locator, title, archived_at, meta FROM link_archive"
                " WHERE account_id = :a AND url_hash = ANY(:h) AND kind = ANY(:ks)"
                " ORDER BY url_hash, (kind = 'page'), archived_at DESC, id DESC"
            ),
            {
                "a": account_id,
                "h": sorted({h for hs in keys.values() for h in hs}),
                "ks": sorted(KINDS if kinds is None else kinds & KINDS),
            },
        )
    ).all()
    by_hash = {row.url_hash: _link(row) for row in rows}
    return {url: by_hash[h] for url, hs in keys.items() for h in hs if h in by_hash}


async def profile_links(
    session: Any, account_id: UUID, limit: int = MAX_PROFILE_LINKS
) -> list[ArchivedLink]:
    """The newest archived copy of every profile page stored for the account, newest first."""
    rows = (
        await session.execute(
            text(
                "SELECT * FROM (SELECT DISTINCT ON (url_hash) url, url_normalized, url_hash,"
                " kind, payload_ref, format, locator, title, archived_at, meta FROM link_archive"
                " WHERE account_id = :a AND kind = 'profile'"
                " ORDER BY url_hash, archived_at DESC, id DESC) p"
                " ORDER BY p.archived_at DESC LIMIT :n"
            ),
            {"a": account_id, "n": limit},
        )
    ).all()
    return [_link(row) for row in rows]


MAX_LIST = 200
# bodies of one index page read side by side (the payload store, no paid call)
_READ_CONCURRENCY = 16


async def list_latest(
    session: Any,
    account_id: UUID,
    *,
    kind: str | None,
    offset: int,
    limit: int,
    kinds: frozenset[str] | None = None,
) -> tuple[list[ArchivedLink], int]:
    """The copy of each address of the account (the newest structured record, else the newest
    page copy), newest capture first, one page of it and the number of addresses (``kind`` None:
    every kind in ``kinds``, default all). ``limit`` is held to ``MAX_LIST``. A client route
    passes ``kinds=CLIENT_KINDS``: a ``kind`` outside it
    lists nothing. One pass over the account's rows gives the page and the count."""
    allowed = KINDS if kinds is None else kinds & KINDS
    if kind is not None and kind not in allowed:
        return [], 0
    wanted = [kind] if kind is not None else sorted(allowed)
    cap = max(1, min(limit, MAX_LIST))
    args: dict[str, Any] = {"a": account_id, "ks": wanted}
    newest = (
        "SELECT DISTINCT ON (url_hash) url, url_normalized, url_hash, kind, payload_ref, format,"
        " locator, title, archived_at, meta, id FROM link_archive"
        " WHERE account_id = :a AND kind = ANY(:ks)"
        " ORDER BY url_hash, (kind = 'page'), archived_at DESC, id DESC"
    )
    rows = (
        await session.execute(
            text(
                f"WITH n AS ({newest}) SELECT n.*, count(*) OVER () AS total FROM n"
                " ORDER BY n.archived_at DESC, n.id DESC OFFSET :o LIMIT :n"
            ),
            {**args, "o": max(offset, 0), "n": cap},
        )
    ).all()
    if rows:
        total = int(rows[0].total)
    elif offset > 0:  # a page past the end: the count still answers
        total = int(
            (
                await session.execute(
                    text(f"SELECT count(*) FROM ({newest}) n"),  # noqa: S608 - fixed text
                    args,
                )
            ).scalar_one()
        )
    else:
        total = 0
    return [_link(row) for row in rows], total


async def list_items(
    payloads: PayloadStore,
    links: Sequence[ArchivedLink],
    *,
    readable: Callable[[str], bool] = lambda _ref: True,
) -> list[ArchiveListItem]:
    """The index rows for a page of newest copies, from the fields stored beside each row
    (``meta``): no body is read for a row that has them. A row written before migration 0092 has
    none; at most ``LEGACY_BODY_READS`` such bodies are read for one page (the payload store, no
    paid call) and the rest list with the fields blank. The row still lists and the address
    still opens."""
    gate = asyncio.Semaphore(_READ_CONCURRENCY)
    legacy = {
        id(link)
        for link in [x for x in links if x.meta is None and x.format == "json"][:LEGACY_BODY_READS]
    }

    async def one(link: ArchivedLink) -> ArchiveListItem:
        """One archive entry's list item (a legacy copy's record is read from its stored body)."""
        record: dict[str, Any] = dict(link.meta or {})
        if id(link) in legacy and readable(link.payload_ref):
            async with gate:
                try:
                    data = json.loads(await payloads.get(link.payload_ref))
                except Exception:  # noqa: BLE001 - a missing or unreadable body lists blank
                    data = None
            if isinstance(data, dict) and _is_record(data):
                record = {**data}
        return ArchiveListItem(
            url=link.url,
            kind=link.kind,
            archived_at=link.archived_at,
            title=link.title or _str(record.get("title")) or _str(record.get("name")),
            status_at_capture=_str(record.get("status_at_capture")),
            location=_str(record.get("location")),
            requisition=_str(record.get("requisition")),
            record_schema=_str(record.get("schema")),
        )

    return list(await asyncio.gather(*(one(link) for link in links)))


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


async def archived_copy(
    payloads: PayloadStore, link: ArchivedLink, *, max_text: int | None = None
) -> ArchivedCopy:
    """The copy behind one archived link, read from the payload store (``max_text``: cut the
    text, ``copy_from``)."""
    return copy_from(link, await payloads.get(link.payload_ref), max_text=max_text)


async def other_account_terms(session: Any, account_id: UUID) -> list[str]:
    """The names and domains of every other account of the client (colleagues' private
    workspaces included): terms no archived text or table cell of ``account_id`` may carry, used
    only to refuse text that names them and never returned. Call it in a system-scope tenant
    transaction."""
    rows = (
        await session.execute(
            text("SELECT name, domain FROM accounts WHERE id <> :a"), {"a": account_id}
        )
    ).all()
    return [t for row in rows for t in (row.name, row.domain) if t]


async def erase_urls(
    session: Any, account_id: UUID, urls: Iterable[str | None], name: str | None = None
) -> int:
    """Delete the index rows of an erased person's addresses, and the profile rows whose title
    names ``name`` (``erase_archive_urls``, migration 0092: the tenant is the session's, the
    account must hold an erased person). The stored bodies stay in the payload store, which has
    no delete; no route reaches a body without its index row. The number of rows deleted."""
    hashes = sorted({h for u in urls for h in hash_candidates(u)})
    if not hashes and not (name and name.strip()):
        return 0
    deleted = await session.execute(
        text("SELECT erase_archive_urls(:a, CAST(:h AS text[]), :n)"),
        {"a": account_id, "h": hashes, "n": name.strip() if name else None},
    )
    return int(deleted.scalar_one())


TxnFactory = Callable[[UUID], Any]


class LinkArchive:
    """The writer bound to one run: records copies in a transaction of its own, or writes a
    small body for a record (a profile, a detection) and records it."""

    def __init__(
        self,
        txn: TxnFactory,
        tenant_id: UUID,
        account_id: UUID,
        run_id: UUID | None,
        payloads: PayloadStore,
        clock: Any,
    ) -> None:
        self.txn = txn
        self.tenant_id = tenant_id
        self.account_id = account_id
        self.run_id = run_id
        self.payloads = payloads
        self.clock = clock

    def now(self) -> datetime:
        """The clock's time (aware UTC)."""
        value: datetime = self.clock.now()
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    async def record(self, entries: Iterable[ArchiveEntry]) -> int:
        """Record copies in one transaction of their own."""
        rows = list(entries)
        if not rows:
            return 0
        async with self.txn(self.tenant_id) as s:
            return await record(s, self.tenant_id, self.account_id, self.run_id, rows)

    async def record_in(self, session: Any, entries: Iterable[ArchiveEntry]) -> int:
        """Record copies in the caller's transaction."""
        return await record(session, self.tenant_id, self.account_id, self.run_id, entries)

    async def entry_for_text(
        self,
        url: str,
        kind: str,
        body_text: str,
        *,
        title: str | None = None,
        source_ref: str | None = None,
        archived_at: datetime | None = None,
    ) -> ArchiveEntry:
        """Store a JSON body for a record the archive copies itself; the entry to record."""
        when = archived_at or self.now()
        title = title or title_of(body_text)
        body = archive_body(
            url, body_text, title=title, captured_at=when, kind=kind, source_ref=source_ref
        )
        ref = await self.payloads.put(archive_key(self.tenant_id, self.account_id, body), body)
        return ArchiveEntry(
            url=url,
            kind=kind,
            payload_ref=ref,
            archived_at=when,
            format="json",
            title=title,
            source_ref=source_ref,
        )

    async def entry_for_record(
        self,
        url: str,
        kind: str,
        record: dict[str, Any],
        *,
        title: str | None = None,
        source_ref: str | None = None,
        archived_at: datetime | None = None,
    ) -> ArchiveEntry:
        """Store a structured record (``archive_records``) under a key made of what it says and
        return the entry to record. A record the archive already holds (an unchanged page seen
        again) is not written a second time; changed content is a new key, a new copy."""
        key = archive_key(self.tenant_id, self.account_id, AR.identity_bytes(record))
        async with self.txn(self.tenant_id) as s:
            held = (
                await s.execute(
                    text(
                        "SELECT 1 FROM link_archive WHERE account_id = :a AND payload_ref = :k"
                        " LIMIT 1"
                    ),
                    {"a": self.account_id, "k": key},
                )
            ).first()
        if held is None:
            await self.payloads.put(key, AR.snapshot_bytes(record))
        return ArchiveEntry(
            url=url,
            kind=kind,
            payload_ref=key,
            archived_at=archived_at or self.now(),
            format="json",
            title=(title or None) and title[:MAX_TITLE],
            source_ref=source_ref,
            meta=AR.index_meta(record),
        )

    async def put_text(
        self,
        url: str,
        kind: str,
        body_text: str,
        *,
        title: str | None = None,
        source_ref: str | None = None,
        archived_at: datetime | None = None,
    ) -> str:
        """Store and record a copy in a transaction of its own; the payload key."""
        entry = await self.entry_for_text(
            url, kind, body_text, title=title, source_ref=source_ref, archived_at=archived_at
        )
        await self.record([entry])
        return entry.payload_ref
