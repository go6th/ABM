"""Tech Stack and Teams mapping (pure).

The Platform Ranking (owner's master prompt v2 section 7.3, patterns copied from the reference
build script) is built from the postings of the run: a platform of
``config/platform_dictionary.yaml`` is named by a posting when its pattern matches the title,
description and platform sentences; the base is the postings in the window that name at least one
platform; each posting counts once per platform. Open now counts the postings not closed; Last
seen is the latest last-seen date of the postings (the first-seen date when the feed gave none).
The rank and share are workbook formulas; their values are computed here for the recalculation
check (B12). "In confirmed stack" is Yes when a technology name of the organisation's stack
matches the platform's pattern, No when none does, Not pulled when no stack was stored.

The Confirmed stack sheet lists the organisation's tech stack rows as the lookup returned them
(``SnapshotContent.stack``: business function, category, technology and its postings count), in
the lookup's order. The lane comes from the platform words of ``workbooks.tech.stack_lanes``
found in the technology name. A name on the scope's never_report list is masked.

The technology detections stay available (``TechModel.rows``): each is a Lead (PRD s3.4 step 6),
the Evidence file's part H lists them, and their source codes are cited from here so the Evidence
register keeps their pages. No sheet of this workbook prints them.

Platform teams: rows appear when ``SnapshotContent.teams`` has rows (no pipeline stores teams yet:
the provider operation is not specified); otherwise the sheet carries the visible "None recorded"
line under its header.
"""

from __future__ import annotations

import re
from datetime import date

from exports.config import ExportConfig, PlatformEntry
from exports.content import PostingRow
from exports.models import CountLine, RankRow, StackLine, TeamLine, TechModel, TechRow
from exports.readers.common import MapContext, Mapped, digest, ref
from exports.readers.jobs import posting_text
from exports.verify import stack_key
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
    """The latest last-seen date of the postings, the first-seen date for a posting the feed gave
    no last seen; ``not_dated`` when none has a date (the reference's ``l`` else ``f``)."""
    known = [d for d in ((p.last_seen or p.first_seen) for p in matches) if d is not None]
    return max(known).isoformat() if known else not_dated


def stack_lane(cfg: ExportConfig, technology: str) -> str:
    """The Confirmed stack's Lane label for a technology name: the last platform word of
    ``workbooks.tech.stack_lanes`` found in the lower-cased name, blank when it holds none."""
    low = technology.casefold()
    lane = ""
    for word, label in cfg.rules.workbooks.tech.stack_lanes.items():
        if word.casefold() in low:
            lane = label
    return lane


def _date_text(d: date) -> str:
    """``19 October 2026``."""
    return f"{d.day} {d:%B %Y}"


def _rank_row(
    ctx: MapContext,
    e: PlatformEntry,
    matches: list[PostingRow],
    base: int,
    stack_names: tuple[str, ...],
) -> RankRow:
    labels = ctx.rules.labels
    count = len(matches)
    share = count / base if base else 0.0
    if not stack_names:
        in_stack = labels.not_pulled
    else:
        in_stack = labels.yes if any(e.names(n) for n in stack_names) else labels.no
    read = sum(1 for p in matches if p.full_text_read)
    return RankRow(
        platform=e.name,
        category=e.category,
        lane=labels.tech_lane_label(e.lane),
        count=count,
        share=share,
        rank=rank_of(count, share),
        open_now=sum(1 for p in matches if p.status != "closed"),
        last_seen=_last_seen(matches, labels.not_dated),
        in_stack=in_stack,
        read_checked=labels.yes if read >= min(3, count) else labels.no,
    )


def map_tech(ctx: MapContext) -> Mapped[TechModel]:
    """The Confirmed stack rows (lookup order), the Platform Ranking from the run's postings, the
    teams when stored, and the detection rows (sorted by technology, refs ``D-0001``..) the
    Evidence file lists."""
    c = ctx.content
    where = "tech"
    labels = ctx.rules.labels
    texts = ctx.rules.sheet_texts.tech
    cap = ctx.rules.limits.row_caps.detections
    if ctx.capped(len(c.detections), cap, where) or ctx.capped(len(c.stack), cap, where):
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
            lane = labels.tech_lane_label(entry.lane)
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

    # the Confirmed stack: the organisation's tech stack in the lookup's order
    stack: list[StackLine] = []
    for x in c.stack:
        if not ctx.printable(x.technology, where, x.id):
            continue
        technology = ctx.rendered(x.technology, where)
        stack.append(
            StackLine(
                row_id=x.id,
                business_function=ctx.rendered(x.business_function, where),
                category=ctx.rendered(x.category, where),
                technology=technology,
                postings=x.postings,
                lane=stack_lane(ctx.cfg, technology),
            )
        )
    if len(stack) != len(c.stack):
        return Mapped(None)
    stack_names = tuple(line.technology for line in stack)

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
    all_rows = [_rank_row(ctx, e, matches[e.name], base, stack_names) for e in ctx.cfg.platforms]
    # most postings first, ties in the dictionary's order (the reference's stable sort)
    ranking = tuple(sorted((r for r in all_rows if r.count >= 2), key=lambda r: -r.count))
    seen_once = tuple(r for r in all_rows if r.count == 1)
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

    # the Platform teams (rows only when a pipeline stored teams)
    teams = tuple(
        TeamLine(
            row_id=t.id,
            team=ctx.rendered(t.name, where),
            lane_technologies=ctx.rendered(t.lane_technologies, where),
            postings=t.postings,
            first_activity=t.first_activity or "",
            last_activity=t.last_activity or "",
            technologies=ctx.rendered(t.technologies, where),
            locations=ctx.rendered(t.locations, where),
            job_functions=ctx.rendered(t.job_functions, where),
        )
        for t in c.teams
    )
    account = c.account_name
    if teams and c.teams_matching and c.teams_since is not None:
        teams_title = texts.teams_title_counted.format(
            account=account,
            shown=len(teams),
            matching=c.teams_matching,
            since=_date_text(c.teams_since),
        )
    else:
        teams_title = texts.teams_title.format(account=account)
    sheets = ctx.rules.workbooks.tech
    teams_note = (
        texts.teams_listed.format(teams=len(teams), sheet=sheets.teams_sheet)
        if teams
        else ctx.rules.none_found.teams
    )

    sheet = sheets.sheet
    counts = (
        CountLine(
            key="detections.rows",
            label="Detections in this file",
            value=len(rows),
            base="technology detections recorded for this run",
        ),
        CountLine(
            key="detections.named",
            label="Detections named in at least one posting",
            value=named_total,
            base="technology detections recorded for this run",
        ),
        CountLine(
            key="detections.withheld",
            label="Detections not listed (no public evidence page)",
            value=withheld,
            base="detections recorded for this run",
        ),
        CountLine(
            key="file.rows",
            label="Technologies in this file",
            value=len(stack),
            base=f"technologies listed on the {sheet} sheet",
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
        account=account,
        as_of=c.as_of,
        rows=tuple(rows),
        counts=counts,
        stack=tuple(stack),
        teams=teams,
        teams_note=teams_note,
        teams_sheet_note=texts.teams_note,
        ranking=ranking,
        seen_once=seen_once,
        base=base,
        postings_in_window=len(window),
        rank_counts=rank_counts,
        title=texts.title.format(account=account),
        base_line=texts.base_line.format(base=base, postings=len(window), months=months),
        notes=(texts.rank_rule, texts.count_rule, texts.signal_rule, texts.research_by),
        stack_title=texts.stack_title.format(account=account),
        stack_note=texts.stack_note.format(as_of=_date_text(c.as_of)),
        teams_title=teams_title,
    )
    return Mapped(
        model,
        row_ids=tuple(x.row_id for x in stack),
        digests=tuple(digest(x) for x in stack),
        refs=tuple(stack_key(x.business_function, x.category, x.technology) for x in stack),
        cited=frozenset(r.source_code for r in rows),
    )
