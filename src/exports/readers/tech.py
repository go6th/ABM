"""Tech Stack and Teams mapping (pure).

The Platform Ranking (owner's master prompt v2 section 7.3) is built from the postings of the run:
a platform of ``config/platform_dictionary.yaml`` is named by a posting when its pattern matches
the title, description and platform sentences; the base is the postings in the window that name
at least one platform; each posting counts once per platform. The rank and share are workbook
formulas; their values are computed here for the recalculation check (B12).

Each detection keeps its first and last seen dates and is labelled a Lead (PRD s3.4 step 6: a
date range in a detection feed is a lead, not a finding). Its "Postings naming it" counts the
postings of the Job Postings file that name the technology; its category and lane come from the
platform dictionary when the technology is one of its platforms (blank otherwise). The business
function of the reference's confirmed stack is not stored by any pipeline, so it is not shown.
No team data is stored by any pipeline yet, so the Platform teams sheet carries the visible "None
recorded" note under its header.
"""

from __future__ import annotations

import re
from datetime import date

from exports.config import PlatformEntry
from exports.content import PostingRow
from exports.models import CountLine, RankRow, TechModel, TechRow
from exports.readers.common import MapContext, Mapped, digest, ref
from exports.readers.jobs import posting_text
from jobs.window import months_before

RANK_LETTERS = ("A", "B", "C")


def names_technology(posting: PostingRow, technology: str) -> bool:
    """True when the posting names the technology as a whole word, any case."""
    pattern = re.compile(rf"(?<!\w){re.escape(technology.strip())}(?!\w)", re.IGNORECASE)
    haystack = "\n".join(
        (*posting.platforms, *posting.platform_sentences, posting.title or "", posting.description)
    )
    return bool(technology.strip()) and bool(pattern.search(haystack))


def in_window(p: PostingRow, start: date) -> bool:
    """A dated posting counts when posted (else first seen) on or after ``start``; an undated
    posting counts."""
    when = p.posted_at or p.first_seen
    return when is None or when >= start


def rank_of(count: int, share: float) -> str:
    """A, B, C or blank by the owner's rule (section 7.3)."""
    if share >= 0.15 and count >= 5:
        return "A"
    if share >= 0.05 and count >= 3:
        return "B"
    return "C" if count >= 2 else ""


def _last_seen(matches: list[PostingRow], not_dated: str) -> str:
    dates = [p.posted_at or p.first_seen for p in matches]
    known = [d for d in dates if d is not None]
    return max(known).isoformat() if known else not_dated


def _rank_row(ctx: MapContext, e: PlatformEntry, matches: list[PostingRow], base: int) -> RankRow:
    labels = ctx.rules.labels
    count = len(matches)
    share = count / base if base else 0.0
    detections = ctx.content.detections
    if not detections:
        in_stack = labels.not_pulled
    else:
        in_stack = labels.yes if any(e.names(d.technology) for d in detections) else labels.no
    read = sum(1 for p in matches if p.full_text_read)
    return RankRow(
        platform=e.name,
        category=e.category,
        lane=labels.lane[e.lane] if e.lane else labels.lane_outside,
        count=count,
        share=share,
        rank=rank_of(count, share),
        open_now=sum(1 for p in matches if p.status == "open"),
        last_seen=_last_seen(matches, labels.not_dated),
        in_stack=in_stack,
        read_checked=labels.yes if read >= min(3, count) else labels.no,
    )


def map_tech(ctx: MapContext) -> Mapped[TechModel]:
    """Detection rows sorted by technology (then id), refs ``D-0001``..; the Platform Ranking
    from the run's postings."""
    c = ctx.content
    where = "tech"
    labels = ctx.rules.labels
    texts = ctx.rules.sheet_texts.tech
    if ctx.capped(len(c.detections), ctx.rules.limits.row_caps.detections, where):
        return Mapped(None)
    postings = c.postings
    rows: list[TechRow] = []
    named_total = 0
    for i, d in enumerate(c.detections, start=1):
        code = ctx.code_or_withhold(d.source_code, d.source_url, where, d.id)
        if not ctx.printable(d.technology, where, d.id) or code is None:
            continue
        n = sum(1 for p in postings if names_technology(p, d.technology))
        named_total += 1 if n else 0
        if postings:
            check = f"Named in {n} of {len(postings)} postings in the Job Postings file"
        else:
            check = "No postings in the Job Postings file to cross check"
        ctx.registry.add(f"tech.cross_check.{i:05d}", n, "postings in the Job Postings file", check)
        entry = next((e for e in ctx.cfg.platforms if e.names(d.technology)), None)
        lane = ""
        if entry is not None:
            lane = labels.lane[entry.lane] if entry.lane else labels.lane_outside
        rows.append(
            TechRow(
                ref=ref("D", i),
                row_id=d.id,
                category=entry.category if entry is not None else "",
                technology=ctx.rendered(d.technology, where),
                postings=n,
                lane=lane,
                first_seen=ctx.rendered(d.first_seen or "Not given", where),
                last_seen=ctx.rendered(d.last_seen or "Not given", where),
                grade=ctx.rules.labels.detection_grade,
                source_code=code,
            )
        )
    withheld = ctx.withheld_in(where)
    if len(rows) + withheld != len(c.detections):
        return Mapped(None)

    # the Platform Ranking over the postings of the window
    months = ctx.cfg.scope.postings_window_months
    start = months_before(c.as_of, months)
    window = [p for p in postings if in_window(p, start)]
    texts_of = {p.posting_key: posting_text(p) for p in window}
    matches = {
        e.name: [p for p in window if e.names(texts_of[p.posting_key])] for e in ctx.cfg.platforms
    }
    base_keys = {p.posting_key for m in matches.values() for p in m}
    base = len(base_keys)
    all_rows = [_rank_row(ctx, e, matches[e.name], base) for e in ctx.cfg.platforms]
    ranking = tuple(
        sorted((r for r in all_rows if r.count >= 2), key=lambda r: (-r.count, r.platform))
    )
    seen_once = tuple(sorted((r for r in all_rows if r.count == 1), key=lambda r: r.platform))
    rank_counts = tuple((x, sum(1 for r in ranking if r.rank == x)) for x in RANK_LETTERS)
    base_text = "postings in the window that name at least one platform"
    ctx.registry.add(
        "tech.ranking.base",
        base,
        f"postings in the Job Postings file within {months} months",
        f"{base} postings name at least one platform",
    )
    ctx.registry.add(
        "tech.ranking.window",
        len(window),
        "postings in the Job Postings file",
        f"{len(window)} postings within {months} months or undated",
    )
    for i, r in enumerate(ranking):
        ctx.registry.add(
            f"tech.ranking.{i:03d}.count",
            r.count,
            base_text,
            f"{r.count} postings name {r.platform}",
        )
        ctx.registry.add(
            f"tech.ranking.{i:03d}.open",
            r.open_now,
            f"postings naming {r.platform}",
            f"{r.open_now} open now",
        )
    for i, r in enumerate(seen_once):
        ctx.registry.add(
            f"tech.seen_once.{i:03d}", r.count, base_text, f"{r.count} posting names {r.platform}"
        )
    for letter, n in rank_counts:
        ctx.registry.add(
            f"tech.ranking.rank.{letter}", n, "ranked platforms", f"{n} platforms ranked {letter}"
        )

    sheet = ctx.rules.workbooks.tech.sheet
    counts = (
        CountLine(
            key="file.rows",
            label="Detections in this file",
            value=len(rows),
            base=f"detections listed on the {sheet} sheet",
        ),
        CountLine(
            key="file.named",
            label="Detections named in at least one posting",
            value=named_total,
            base=f"detections listed on the {sheet} sheet",
        ),
        CountLine(
            key="file.withheld",
            label="Detections not listed (no public evidence page)",
            value=withheld,
            base="detections recorded for this run",
        ),
        CountLine(
            key="file.ranked",
            label="Platforms ranked (two postings or more)",
            value=len(ranking),
            base="platforms of the platform dictionary",
        ),
    )
    for count in counts:
        ctx.registry.add(
            f"tech.{count.key}", count.value, count.base, f"{count.value} {count.label}"
        )
    model = TechModel(
        account=c.account_name,
        as_of=c.as_of,
        rows=tuple(rows),
        counts=counts,
        teams_note=ctx.rules.none_found.teams,
        ranking=ranking,
        seen_once=seen_once,
        base=base,
        postings_in_window=len(window),
        rank_counts=rank_counts,
        title=texts.title.format(account=c.account_name),
        base_line=texts.base_line.format(base=base, postings=len(window), months=months),
        notes=(texts.rank_rule, texts.count_rule, texts.signal_rule, texts.research_by),
        stack_title=texts.stack_title.format(account=c.account_name),
        stack_note=texts.stack_note.format(as_of=c.as_of.isoformat()),
        teams_title=texts.teams_title.format(account=c.account_name),
    )
    return Mapped(
        model,
        row_ids=tuple(r.row_id for r in rows),
        digests=tuple(digest(r) for r in rows),
        refs=tuple(r.ref for r in rows),
        cited=frozenset(r.source_code for r in rows),
    )
