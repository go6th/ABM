"""Bounded pagination and bounded provider-job polling.

Every loop has a hard cap, a deadline and a recorded exit reason. Pagination stops on
``exhausted`` (the provider says there is no more), ``max_pages``, ``max_items``,
``max_bytes``, ``deadline`` or ``loop_detected`` (a repeated cursor or an identical
page). Anything other than ``exhausted`` makes the result Partial, never Done.
Records are de-duplicated across pages by their stable key. Each page is its own
ledger key, so a resumed pull replays stored pages for free and continues after them.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from account_intelligence.connectors.base import Clock, ParsedPage, Sleep, StopReason
from account_intelligence.connectors.errors import ProviderJobTimeout
from account_intelligence.connectors.models import Record


@dataclass(frozen=True)
class PageLimits:
    max_pages: int
    max_items: int
    max_bytes: int

    def __post_init__(self) -> None:
        if min(self.max_pages, self.max_items, self.max_bytes) < 1:
            raise ValueError("pagination caps must be positive")


@dataclass(frozen=True)
class FetchedPage:
    parsed: ParsedPage
    body_size: int
    body_digest: str  # sha256 of the raw body: identical pages mean a loop
    raw_ref: str


@dataclass
class PaginationResult:
    records: list[Record] = field(default_factory=list)
    raw_refs: list[str] = field(default_factory=list)
    pages: int = 0
    items_read: int = 0
    bytes_read: int = 0
    stop_reason: StopReason = "exhausted"

    @property
    def complete(self) -> bool:
        return self.stop_reason == "exhausted"


FetchPage = Callable[[str | None, int], Awaitable[FetchedPage]]


async def paginate(
    fetch_page: FetchPage,
    limits: PageLimits,
    clock: Clock,
    *,
    deadline: float | None,
    start_cursor: str | None = None,
) -> PaginationResult:
    """Drive ``fetch_page(cursor, page_number)`` until a stop condition. Always terminates:
    at most ``max_pages`` iterations."""
    result = PaginationResult()
    seen_keys: set[str] = set()
    seen_cursors: set[str | None] = set()
    seen_digests: set[str] = set()
    cursor = start_cursor
    for page_number in range(1, limits.max_pages + 1):
        if deadline is not None and clock.monotonic() >= deadline:
            result.stop_reason = "deadline"
            return result
        seen_cursors.add(cursor)
        page = await fetch_page(cursor, page_number)
        result.pages += 1
        result.raw_refs.append(page.raw_ref)
        result.bytes_read += page.body_size
        if page.body_digest in seen_digests:
            result.stop_reason = "loop_detected"
            return result
        seen_digests.add(page.body_digest)
        result.items_read += page.parsed.items_read
        for record in page.parsed.records:
            key = record.dedupe_key()
            if key in seen_keys:
                continue
            seen_keys.add(key)
            result.records.append(record)
            if len(result.records) >= limits.max_items:
                result.stop_reason = "max_items"
                return result
        if result.bytes_read >= limits.max_bytes:
            result.stop_reason = "max_bytes"
            return result
        nxt = page.parsed.next_cursor
        if nxt is None:
            result.stop_reason = "exhausted"
            return result
        if nxt in seen_cursors:
            result.stop_reason = "loop_detected"
            return result
        cursor = nxt
    result.stop_reason = "max_pages"
    return result


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class PollLimits:
    interval_s: float
    jitter_s: float
    max_polls: int
    total_timeout_s: float


async def poll_job(
    poll: Callable[[], Awaitable[ParsedPage | None]],
    limits: PollLimits,
    clock: Clock,
    sleep: Sleep,
    rng: random.Random,
    *,
    deadline: float | None,
    cancel: Callable[[], Awaitable[None]] | None = None,
) -> ParsedPage:
    """Poll an async provider job until ``poll`` returns a page (``None`` = still running).
    Bounded by ``max_polls``, ``total_timeout_s`` and the slice deadline; cancels on timeout."""
    started = clock.monotonic()
    stop_at = started + limits.total_timeout_s
    if deadline is not None:
        stop_at = min(stop_at, deadline)
    for _ in range(limits.max_polls):
        page = await poll()
        if page is not None:
            return page
        wait = limits.interval_s + rng.uniform(0, limits.jitter_s)
        if clock.monotonic() + wait >= stop_at:
            break
        await sleep(wait)
    if cancel is not None:
        await cancel()
    raise ProviderJobTimeout("provider job did not finish in time")
