"""Counts computed from posting rows only (J1). Pure; no function here accepts a count.

Definitions (the contract in ``docs/jobs/counts.md``):

* ``pulled``: raw records returned (live board plus feed), before dedupe; ``deduped``: postings.
* the ANALYSED set is in-window plus undated postings (undated are included and counted, a
  proposal); ``in_window`` counts the analysed set, ``undated`` is its undated part and
  ``out_of_window`` is excluded from everything below.
* ``matched``: analysed postings naming a platform (description or title); ``title_only``: the
  title-only part (Lead candidates, never classified).
* ``read``: matched postings whose complete text was obtained and processed this run (J2);
  hostile postings are never read.
* ``by_class`` sums to ``read`` (title-only read postings are ``unclassified``).
* ``with_platform_sentence``: read postings with a stored sentence; it must equal the read
  postings that name a platform in the description, and every named platform must have its
  sentence (J3). A violation raises ``InvariantViolation`` (the row then ends Partial).

Per-platform blocks overlap (a posting naming two platforms counts once in each and once in the
distinct block); they are never summed. Platforms not searched carry a ``NotSearched`` reason.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from jobs.errors import InvariantViolation
from jobs.models import (
    CountBlock,
    NotSearched,
    Posting,
    PostingCounts,
    RawPosting,
    StatusCounts,
)

CLASS_NAMES = ("admin_owner", "build", "unclassified", "user", "working_tool")


def _status(rows: Sequence[Posting], base: str) -> StatusCounts:
    counts = Counter(row.status for row in rows)
    group = StatusCounts(
        base=base,
        total=len(rows),
        open=counts["open"],
        closed=counts["closed"],
        unknown_status=counts["unknown"],
    )
    if (
        group.open + group.closed + group.unknown_status != group.total
    ):  # pragma: no cover - guard, unreachable by construction
        raise InvariantViolation(f"status counts do not sum to the {base} base")
    return group


def _block(matched: Sequence[Posting]) -> CountBlock:
    read = [p for p in matched if p.read and not p.hostile]
    for p in read:
        named = set(p.platforms)
        if named != {s.platform for s in p.sentences}:
            raise InvariantViolation("a read posting names a platform without its sentence")
    classes: Counter[str] = Counter(p.posting_class for p in read)
    by_class = {name: classes[name] for name in CLASS_NAMES}
    if sum(by_class.values()) != len(read) or set(classes) - set(
        CLASS_NAMES
    ):  # pragma: no cover - guard, unreachable by construction
        raise InvariantViolation("class counts do not sum to the read base")
    with_sentence = sum(bool(p.sentences) for p in read)
    if with_sentence != sum(
        bool(p.platforms) for p in read
    ):  # pragma: no cover - guard, unreachable by construction
        raise InvariantViolation("platform sentence count differs from naming postings")
    if len(read) > len(matched):  # pragma: no cover - guard, unreachable by construction
        raise InvariantViolation("read exceeds matched")
    return CountBlock(
        matched=len(matched),
        read=len(read),
        matched_status=_status(matched, "matched"),
        read_status=_status(read, "read"),
        by_class=by_class,
        with_platform_sentence=with_sentence,
        full_text_read=sum(p.full_text_read for p in matched),
        full_text_missing=sum(p.full_text_missing for p in matched),
        title_only=sum(p.title_only for p in matched),
    )


def compute_counts(
    raw: Sequence[RawPosting],
    postings: Sequence[Posting],
    platforms: Sequence[str],
    not_searched: Mapping[str, str],
) -> PostingCounts:
    """All counts from the raw records and the deduplicated postings; invariants asserted."""
    if len({p.posting_key for p in postings}) != len(postings):
        raise InvariantViolation("postings are not deduplicated")
    analysed = [p for p in postings if p.window_bucket != "out_of_window"]
    matched = [p for p in analysed if p.matched]
    distinct = _block(matched)
    per_platform: dict[str, CountBlock | NotSearched] = {}
    for platform in sorted(platforms):
        if platform in not_searched:
            per_platform[platform] = NotSearched(reason=not_searched[platform])
            continue
        subset = [p for p in matched if platform in p.platforms or platform in p.title_platforms]
        block = _block(subset)
        if (
            block.matched > distinct.matched or block.read > distinct.read
        ):  # pragma: no cover - guard, unreachable by construction
            raise InvariantViolation("a platform count exceeds the distinct count")
        per_platform[platform] = block
    counts = PostingCounts(
        pulled=len(raw),
        deduped=len(postings),
        in_window=len(analysed),
        out_of_window=len(postings) - len(analysed),
        undated=sum(p.window_bucket == "undated" for p in postings),
        hostile=sum(p.hostile for p in postings),
        distinct=distinct,
        per_platform=per_platform,
    )
    chain = (distinct.read, distinct.matched, counts.in_window, counts.deduped, counts.pulled)
    if list(chain) != sorted(chain):
        raise InvariantViolation("count chain read <= matched <= in_window <= deduped <= pulled")
    return counts


def render_count_sentence(block: CountBlock, template: str) -> str:
    """Render one block with its bases; every number comes from the block (J1)."""
    return template.format(
        read=block.read,
        matched=block.matched,
        open=block.read_status.open,
        closed=block.read_status.closed,
    )
