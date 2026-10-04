"""Stable deduplication (J8). Pure.

Two records are the same posting when they share a provider job id or any canonical URL (so a
live-board hit, which has only a URL, merges with its feed record, which has an id and the same
URL). Groups are found with union-find over those identities; titles never merge anything. A
group's key is its smallest provider id when any member has one, else its smallest canonical
URL. Every returned URL is kept.

A record whose URL is not an absolute http(s) URL is rejected and COUNTED, never dropped silently
(J10). So every kept record has a canonical URL, and the content fingerprint of
``normalise.posting_key`` (``weak_key``) is never needed for a posting that survives dedupe.
Output is sorted by key and does not depend on input order; within a group the record with
complete text, then the longer description, then the later retrieval is kept, and a live-board
observation anywhere in the group is kept (it decides open status, J7).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from jobs.models import RawPosting
from jobs.normalise import canonical_url


@dataclass(frozen=True)
class DedupeResult:
    """Merged postings (sorted by key), the rejected count and each key's ledger references."""

    rows: list[RawPosting]
    rejected: int
    call_refs: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _rank(row: RawPosting) -> tuple[bool, int, str, str]:
    return (row.text_complete is True, len(row.description or ""), row.retrieved_at, row.raw_ref)


def _identities(row: RawPosting) -> list[str]:
    """Provider id and every canonical URL; never empty for a kept record (it has a URL)."""
    ids = [f"id:{row.source_id}"] if row.source_id else []
    urls = {canonical_url(u) for u in (row.url, *row.other_urls)} - {""}
    return ids + [f"url:{u}" for u in sorted(urls)]


def dedupe_counted(rows: Iterable[RawPosting]) -> DedupeResult:
    """Merge records of the same posting; reject and count records without a usable URL."""
    kept = []
    rejected = 0
    for row in rows:
        if canonical_url(row.url):
            kept.append(row)
        else:
            rejected += 1
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        # LOOP(union-find path walk): cap=number of identities (path halving); exit=root found
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for row in kept:
        first, *others = _identities(row)
        for other in others:
            a, b = find(first), find(other)
            if a != b:
                parent[max(a, b)] = min(a, b)
    groups: dict[str, list[RawPosting]] = {}
    for row in kept:
        groups.setdefault(find(_identities(row)[0]), []).append(row)

    out: list[RawPosting] = []
    refs: dict[str, tuple[str, ...]] = {}
    for members in groups.values():
        chosen = max(members, key=_rank)
        ids = sorted({m.source_id for m in members if m.source_id})
        all_urls = {u[4:] for m in members for u in _identities(m) if u.startswith("url:")}
        merged = chosen.model_copy(
            update={
                "source_id": ids[0] if ids else chosen.source_id,
                "other_urls": tuple(sorted(all_urls - {canonical_url(chosen.url)})),
                "live_board_present": any(m.live_board_present for m in members),
                "status": chosen.status or next((m.status for m in members if m.status), None),
            }
        )
        out.append(merged)
        refs[_key_of(merged)] = tuple(sorted({m.call_ref for m in members if m.call_ref}))
    out.sort(key=_key_of)
    return DedupeResult(out, rejected, refs)


def _key_of(row: RawPosting) -> str:
    """The posting key of a merged record: smallest id, else smallest canonical URL. Every
    kept record has a canonical URL, so no merged record needs the weak fingerprint."""
    if row.source_id:
        return f"id:{row.source_id}"
    urls = sorted({canonical_url(u) for u in (row.url, *row.other_urls)} - {""})
    return f"url:{urls[0]}"


def group_key(row: RawPosting) -> str:
    """The key of a merged record (as ``dedupe_counted`` assigned it)."""
    return _key_of(row)


def dedupe(rows: list[RawPosting]) -> list[RawPosting]:
    """Merged postings only."""
    return dedupe_counted(rows).rows
