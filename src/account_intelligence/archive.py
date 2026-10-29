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

GUARANTEE(test_a_stored_link_is_archived_and_served_back)
GUARANTEE(test_the_newest_copy_of_an_address_wins)
"""

from __future__ import annotations

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

from account_intelligence.urls import normalize_url, url_hash

KINDS = frozenset({"profile", "posting", "page", "feed_item", "detection"})
FORMATS = frozenset({"json", "text"})
MAX_TITLE = 300
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


@dataclass(frozen=True)
class ArchivedCopy:
    """One archived copy as the client sees it: the address, when it was captured, its text."""

    url: str
    kind: str
    archived_at: datetime
    title: str | None
    text: str


def normalized(url: str | None) -> tuple[str, str] | None:
    """``(url_normalized, url_hash)`` of an http(s) address, None for anything else."""
    if not url:
        return None
    try:
        norm = normalize_url(url)
    except ValueError:
        return None
    return norm, url_hash(norm)


def link_kind(url: str, default: str = "page") -> str:
    """``profile`` for a person's own page on the profile host, else ``default``."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().rstrip(".")
    on_host = host == _PROFILE_HOST or host.endswith("." + _PROFILE_HOST)
    if on_host and _PROFILE_PATH.match(parts.path or ""):
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


def copy_from(link: ArchivedLink, body: bytes) -> ArchivedCopy:
    """The archived copy read out of its stored body (a JSON item's ``text``, or plain text)."""
    if link.format == "text":
        text_value = body.decode("utf-8", "replace")
        title = link.title
    else:
        data = json.loads(body)
        if not isinstance(data, dict) or not isinstance(data.get("text"), str):
            raise ValueError("archived body has no text")
        text_value = data["text"]
        title = link.title or (data.get("title") if isinstance(data.get("title"), str) else None)
    return ArchivedCopy(link.url, link.kind, link.archived_at, title, text_value)


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
                "url": e.url.strip()[:2048],
                "norm": key[0],
                "hash": key[1],
                "kind": e.kind,
                "ref": e.payload_ref,
                "fmt": e.format,
                "loc": e.locator,
                "title": (e.title or None) and e.title[:MAX_TITLE],
                "src": e.source_ref,
                "at": when,
            }
        )
    return out


# the row is owned like its account (private workspaces): the writer is usually a research
# worker in system scope, so the account's owner is named on every insert
_INSERT = text(
    "INSERT INTO link_archive (tenant_id, owner_user_id, account_id, run_id, url,"
    " url_normalized, url_hash, kind, payload_ref, format, locator, title, source_ref,"
    " archived_at) VALUES (:t, coalesce((SELECT a.owner_user_id FROM accounts a"
    " WHERE a.tenant_id = :t AND a.id = :a), app_user()), :a, :r, :url, :norm, :hash, :kind,"
    " :ref, :fmt, :loc, :title, :src, :at)"
    " ON CONFLICT (tenant_id, account_id, url_hash, payload_ref) DO NOTHING"
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
        url=row.url,
        url_normalized=row.url_normalized,
        kind=row.kind,
        payload_ref=row.payload_ref,
        format=row.format,
        archived_at=row.archived_at,
        title=row.title,
        locator=row.locator,
    )


async def lookup(session: Any, account_id: UUID, urls: Sequence[str]) -> dict[str, ArchivedLink]:
    """The newest archived copy of each address, keyed by the address as given (addresses that
    are not http(s), or have no copy, are absent)."""
    keys: dict[str, str] = {}
    for url in urls:
        key = normalized(url)
        if key is not None:
            keys.setdefault(url, key[1])
    if not keys:
        return {}
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT ON (url_hash) url, url_normalized, url_hash, kind, payload_ref,"
                " format, locator, title, archived_at FROM link_archive"
                " WHERE account_id = :a AND url_hash = ANY(:h)"
                " ORDER BY url_hash, archived_at DESC, id DESC"
            ),
            {"a": account_id, "h": sorted(set(keys.values()))},
        )
    ).all()
    by_hash = {row.url_hash: _link(row) for row in rows}
    return {url: by_hash[h] for url, h in keys.items() if h in by_hash}


async def profile_links(
    session: Any, account_id: UUID, limit: int = MAX_PROFILE_LINKS
) -> list[ArchivedLink]:
    """The newest archived copy of every profile page stored for the account, newest first."""
    rows = (
        await session.execute(
            text(
                "SELECT * FROM (SELECT DISTINCT ON (url_hash) url, url_normalized, url_hash,"
                " kind, payload_ref, format, locator, title, archived_at FROM link_archive"
                " WHERE account_id = :a AND kind = 'profile'"
                " ORDER BY url_hash, archived_at DESC, id DESC) p"
                " ORDER BY p.archived_at DESC LIMIT :n"
            ),
            {"a": account_id, "n": limit},
        )
    ).all()
    return [_link(row) for row in rows]


async def archived_copy(payloads: PayloadStore, link: ArchivedLink) -> ArchivedCopy:
    """The copy behind one archived link, read from the payload store."""
    return copy_from(link, await payloads.get(link.payload_ref))


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
