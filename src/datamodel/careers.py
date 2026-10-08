"""Careers URL is visited, never constructed (DM3, roadmap 11.1).

A careers URL is stored only from a ``Visit``: the page was read through ``call_operation`` and
the visit carries the ledger key of that read (the fetch record). A URL built from a pattern (no
fetch record, a non-http scheme, or a page off the account's domain) is refused with
``ConstructedUrl``. The database enforces the same (``ck_accounts_careers_url_visited``). The one
writer of ``accounts.careers_url``.

GUARANTEE(test_a_constructed_careers_url_is_refused)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text


class ConstructedUrl(ValueError):
    """A careers URL without proof of a visit."""


@dataclass(frozen=True)
class Visit:
    """Proof that a page was read: its URL and the fetch record (ledger key of the read)."""

    url: str
    fetch_ref: str
    visited_at: datetime


def check(visit: Visit, domain: str) -> str:
    """The URL to store, or ConstructedUrl (pure)."""
    parts = urlsplit(visit.url)
    host = (parts.hostname or "").lower()
    if not visit.fetch_ref.strip():
        raise ConstructedUrl("no fetch record: the page was not visited")
    if parts.scheme not in ("http", "https"):
        raise ConstructedUrl("not a web page")
    if not (host == domain or host.endswith("." + domain)):
        raise ConstructedUrl("the page is not on the account's domain")
    return visit.url


async def record_visit(s: Any, *, account_id: UUID, domain: str, visit: Visit) -> None:
    """Store a visited careers URL with its fetch record."""
    url = check(visit, domain)
    await s.execute(
        text(
            "UPDATE accounts SET careers_url = :u, careers_url_verified_at = :at,"
            " careers_fetch_ref = :f WHERE id = :a"
        ),
        {"u": url, "at": visit.visited_at, "f": visit.fetch_ref, "a": account_id},
    )
