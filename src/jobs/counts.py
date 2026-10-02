"""Counts derived exclusively from posting rows."""

from collections import Counter

from jobs.errors import InvariantViolation
from jobs.models import CountGroup, Posting, PostingCounts


def _status(rows: list[Posting], base: str) -> CountGroup:
    counts = Counter(row.status for row in rows)
    group = CountGroup(
        base=base, open=counts["open"], closed=counts["closed"], unknown_status=counts["unknown"]
    )
    if group.open + group.closed + group.unknown_status != len(rows):
        raise InvariantViolation("status counts do not match base")
    return group


def compute_counts(
    postings: list[Posting], *, matched_keys: set[str], minimum_text_length: int
) -> PostingCounts:
    """Compute all totals from rows; callers provide match identities, never totals."""
    unique: dict[str, Posting] = {p.posting_key: p for p in postings}
    rows = list(unique.values())
    window = [p for p in rows if p.window_bucket == "in_window"]
    matched = [p for p in window if p.posting_key in matched_keys]
    read = [
        p
        for p in matched
        if p.text_complete is True
        and len(p.description or "") >= minimum_text_length
        and not p.hostile
    ]
    classes = Counter(
        (p.classification.label.value if p.classification else "unclassified") for p in read
    )
    names = {"build", "admin_owner", "working_tool", "user", "unclassified"}
    by_class = {name: classes[name] for name in sorted(names)}
    if sum(by_class.values()) != len(read):
        raise InvariantViolation("classification counts do not match read base")
    per_platform = Counter(platform for p in read for platform in p.platforms)
    with_sentence = sum(bool(p.platforms) for p in read)
    if with_sentence != sum(bool(p.platforms) for p in read):
        raise InvariantViolation("platform sentence count mismatch")
    if not (len(read) <= len(matched) <= len(window) <= len(rows) <= len(postings)):
        raise InvariantViolation("posting count chain violated")
    return PostingCounts(
        pulled=len(postings),
        deduped=len(rows),
        in_window=len(window),
        out_of_window=sum(p.window_bucket == "out_of_window" for p in rows),
        undated=sum(p.window_bucket == "undated" for p in rows),
        matched=len(matched),
        read=len(read),
        matched_status=_status(matched, "matched"),
        read_status=_status(read, "read"),
        by_class=by_class,
        with_platform_sentence=with_sentence,
        full_text_read=sum(p.full_text_read for p in matched),
        full_text_missing=sum(not p.full_text_read for p in matched),
        title_only=sum(not p.platforms for p in matched),
        hostile=sum(p.hostile for p in rows),
        per_platform=dict(per_platform),
    )


def render_count_sentence(counts: PostingCounts) -> str:
    """Render row-derived counts with explicit matched and read bases."""
    return (
        f"{counts.read} postings read of {counts.matched} matched "
        f"({counts.read_status.open} open and {counts.read_status.closed} closed "
        f"of the {counts.read} read)"
    )
