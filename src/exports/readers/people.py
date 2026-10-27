"""People List mapping (pure). The selection and the Summary sheet lines are the people module's
own (``people.export.build_export``, run by the reader); this maps them to the render model in
the shape of the owner's reference People List (master prompt v2 section 9.3; BD, 2026-10-19):
rows ordered by level, priority, lane and name; level, lane, seniority and found-by labels from
``export_rules.yaml labels``; the Summary sheet's title and paragraphs (what the list merges and
from which searches, the rows removed before it, the people excluded by title by name, the
seniority rule, the judgment note) built only from the stored people record and the run's stored
search calls. It attaches the registered source code (B4) and registers every count the Summary
sheet renders (B6). The Level and Lane tables are workbook formulas over the People sheet; their
code-computed values live on the model for the recalculation check (B12)."""

from __future__ import annotations

from collections import Counter
from datetime import date

from exports.config import Labels, PeopleSheetTexts
from exports.content import PersonRow, PullCall, SnapshotContent
from exports.models import (
    ExcludedPerson,
    LaneCount,
    LevelCount,
    PeopleModel,
    PeopleRow,
    SummaryLine,
)
from exports.readers.common import MapContext, Mapped, digest, ref

# the stored search operations behind the list (connectors.base Operation values)
TITLE_SEARCH = "people_by_title"
ASSOCIATION_SEARCH = "people_by_technology"
_PLURAL = {"row": "rows", "search": "searches", "record": "records", "person": "people"}


def _id(value: str | None) -> str:
    """A stored level or lane id as the rubric spells it (``senior manager`` becomes
    ``senior_manager``)."""
    return (value or "").strip().casefold().replace(" ", "_")


def _n(n: int, word: str) -> str:
    """``1 row``, ``2 rows``, ``12,278 records``: a count with its word."""
    return f"{n:,} {word if n == 1 else _PLURAL[word]}"


def _and(items: list[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _long_date(d: date) -> str:
    """``19 October 2026`` (the reference's date form)."""
    return f"{d.day} {d:%B} {d.year}"


def _lane_order(labels: Labels, scope_lanes: tuple[str, ...]) -> list[str]:
    """Lane ids in the People List's order: ``people_lane`` first, then any other scope lane."""
    ids = [_id(x) for x in labels.people_lane]
    return ids + [_id(x) for x in scope_lanes if _id(x) not in ids]


def _lane_ids(p: PersonRow, order: list[str]) -> list[str]:
    rank = {lane: n for n, lane in enumerate(order)}
    ids = [_id(x) for x in p.lanes.split(",") if x.strip()]
    return sorted(dict.fromkeys(ids), key=lambda x: rank.get(x, len(rank)))


def _merges(
    c: SnapshotContent, texts: PeopleSheetTexts, by_origin: Counter[str], ctx: MapContext
) -> str:
    """ "It merges ..." from the stored search calls and the listed people by origin; empty when
    nothing was searched or listed."""
    title_calls = [x for x in c.pull_calls if x.operation == TITLE_SEARCH]
    assoc_calls = [x for x in c.pull_calls if x.operation == ASSOCIATION_SEARCH]
    parts: list[str] = []

    def details(calls: list[PullCall], origin: str) -> str:
        out = []
        if calls:
            out.append(texts.searches.format(searches=_n(len(calls), "search")))
            counted = [x.result_count for x in calls if x.result_count is not None]
            if counted:
                out.append(texts.matches.format(records=_n(sum(counted), "record")))
        out.append(texts.on_list.format(people=_n(by_origin[origin], "person")))
        return ", ".join(out)

    if title_calls or by_origin["title_search"]:
        domains = sorted({x.domain.casefold() for x in title_calls if x.domain})
        if not domains and c.domain:
            domains = [c.domain.casefold()]
        label = (
            texts.merge_title.format(domains=_and(domains)) if domains else texts.merge_title_any
        )
        parts.append(f"{label} ({details(title_calls, 'title_search')})")
    if assoc_calls or by_origin["association"]:
        parts.append(f"{texts.merge_association} ({details(assoc_calls, 'association')})")
    if by_origin["mention"]:
        parts.append(f"{texts.merge_mention} ({details([], 'mention')})")
    for key, n in (
        ("people.searches.title", len(title_calls)),
        ("people.searches.association", len(assoc_calls)),
        (
            "people.searches.title.records",
            sum(x.result_count or 0 for x in title_calls),
        ),
        (
            "people.searches.association.records",
            sum(x.result_count or 0 for x in assoc_calls),
        ),
    ):
        ctx.registry.add(key, n, "stored search calls of this run", f"{n} {key}")
    for origin in ("title_search", "association", "mention"):
        n = by_origin[origin]
        ctx.registry.add(
            f"people.listed.{origin}", n, "people on the People List", f"{n} found by {origin}"
        )
    return texts.merges.format(parts=_and(parts)) if parts else ""


def _removed(lines: tuple[SummaryLine, ...], withheld: int, texts: PeopleSheetTexts) -> str:
    """ "Rows removed before this list: ..." from the people module's Summary lines (in scope
    and the title exclusions, listed apart, are left out); empty when nothing was removed."""
    items: list[str] = []
    for line in lines:
        if line.count == 0 or (line.status == "in scope" and not line.reason):
            continue
        if line.status == "excluded" and line.reason in texts.exclusion_reasons:
            continue  # named in the exclusions paragraph
        template = texts.removed_items.get(f"{line.status}|{line.reason}")
        rows = _n(line.count, "row")
        if template is None:
            items.append(
                texts.removed_other.format(rows=rows, status=line.status, reason=line.reason)
            )
        else:
            items.append(template.format(rows=rows))
    if withheld:
        items.append(texts.withheld.format(rows=_n(withheld, "row")))
    return texts.removed.format(items="; ".join(items)) if items else ""


def _exclusions(
    excluded: tuple[ExcludedPerson, ...], texts: PeopleSheetTexts, limit: int
) -> list[str]:
    """The exclusions paragraph (one cell, continued on the next rows only past the cell
    limit): the reasons present, the count, then every name with the title as reported."""
    if not excluded:
        return [texts.no_exclusions]
    present = {p.reason for p in excluded}
    known = [label for key, label in texts.exclusion_reasons.items() if key in present]
    other = sorted(present - set(texts.exclusion_reasons))
    reasons = _and(known + other)
    reasons = reasons[:1].upper() + reasons[1:]
    names = [f"{p.name} ({p.title})" if p.title else p.name for p in excluded]
    lead = texts.exclusions.format(reasons=reasons, rows=_n(len(excluded), "row"), names="{names}")
    head, _, tail = lead.partition("{names}")
    out: list[str] = []
    current = head
    for k, name in enumerate(names):
        piece = name + (tail if k == len(names) - 1 else "; ")
        if len(current) + len(piece) > limit and current.strip():
            out.append(current.rstrip())
            current = ""
        current += piece
    out.append(current.rstrip())
    return out


def map_people(ctx: MapContext) -> Mapped[PeopleModel]:
    """People rows ordered as the reference orders them (level, priority, lane, name), refs
    ``P-0001``..; the Summary sheet carries the title, the list's provenance, the rows removed,
    the people excluded by title by name, the seniority rule, the judgment note and the Level
    and Lane tables."""
    c = ctx.content
    where = "people"
    labels = ctx.rules.labels
    texts = ctx.rules.sheet_texts.people
    if ctx.capped(len(c.people), ctx.rules.limits.row_caps.people, where):
        return Mapped(None)
    lane_order = _lane_order(labels, tuple(ctx.cfg.scope.lanes))
    lane_labels = list(dict.fromkeys(labels.people_lane_label(x) for x in lane_order))
    lane_labels.append(labels.lane_none)
    lane_rank = {label: n for n, label in enumerate(dict.fromkeys(lane_labels))}
    level_rank = {label: n for n, label in enumerate(labels.people_level_order)}

    def order(p: PersonRow) -> tuple[int, str, int, str, str]:
        ids = _lane_ids(p, lane_order)
        lane = labels.people_lane_label(ids[0] if ids else None)
        level = level_rank.get(labels.level_label(p.level), len(level_rank))
        lane_n = lane_rank.get(lane, len(lane_rank))
        return (level, p.priority, lane_n, p.name.casefold(), p.profile_url)

    rows: list[PeopleRow] = []
    by_origin: Counter[str] = Counter()
    for i, p in enumerate(sorted(c.people, key=order), start=1):
        code = ctx.code_or_withhold(p.source_code, p.profile_url, where, p.id)
        name = ctx.rendered(p.name, where)
        if not name.strip():
            ctx.fail("name_missing", where, p.id)  # nothing printable once sanitised
            continue
        if code is None:
            continue
        found_by = labels.found_by.get(p.origin)
        if found_by is None:
            ctx.fail("origin_vocabulary", where, p.id)
            continue
        lane_ids = _lane_ids(p, lane_order)
        primary = lane_ids[0] if lane_ids else None
        lane = labels.people_lane_label(primary)
        notes = []
        also = [labels.people_lane_label(x) for x in lane_ids[1:]]
        also = [x for x in dict.fromkeys(also) if x != lane]
        if also:
            notes.append(f"Also in lane: {', '.join(also)}.")
        if p.former_employers is None:
            former = labels.not_pulled
        else:
            former = ctx.rendered(", ".join(p.former_employers), where)
        by_origin[p.origin] += 1
        rows.append(
            PeopleRow(
                ref=ref("P", i),
                row_id=p.id,
                number=len(rows) + 1,
                name=name,
                title=ctx.rendered(p.title, where),
                level=labels.level_label(p.level),
                level_id=_id(p.level),
                meets_seniority=labels.meets_seniority(p.level),
                lanes=lane,
                lane_id=primary,
                priority=ctx.rendered(p.priority, where),
                former_employers=former,
                found_by=found_by,
                profile_url=p.profile_url,
                second_profile_url=p.second_profile_url or None,
                location=ctx.rendered(p.location, where) if p.location else "",
                note=ctx.rendered(" ".join(notes), where),
                source_code=code,
            )
        )
    withheld = ctx.withheld_in(where)
    if len(rows) + withheld != len(c.people):
        return Mapped(None)
    summary: tuple[SummaryLine, ...]
    excluded: tuple[ExcludedPerson, ...] = ()
    if c.people_counts is None:
        summary = (SummaryLine(status=ctx.rules.none_found.text, reason="", count=0),)
        sentences: tuple[str, ...] = ()
    else:
        summary = tuple(
            SummaryLine(
                status=ctx.rendered(status, where),
                reason=ctx.rendered(reason, where),
                count=n,
            )
            for status, reason, n in c.people_counts.summary
        )
        sentences = tuple(ctx.rendered(s, where) for s in c.people_counts.sentences)
        excluded = tuple(
            ExcludedPerson(
                name=ctx.rendered(n, where),
                reason=ctx.rendered(r, where),
                title=ctx.rendered(t, where) if t else "",
            )
            for n, r, t in c.people_counts.excluded
            if ctx.rendered(n, where).strip()
        )
    levels = tuple(
        LevelCount(
            label=label,
            people=sum(1 for r in rows if r.level == label),
            priority_a=sum(1 for r in rows if r.level == label and r.priority == "A"),
        )
        for label in labels.people_level_order
    )
    senior = [r for r in rows if r.meets_seniority]
    lanes_table = tuple(
        LaneCount(
            label=label,
            a=sum(1 for r in senior if r.lanes == label and r.priority == "A"),
            b=sum(1 for r in senior if r.lanes == label and r.priority == "B"),
            c=sum(1 for r in senior if r.lanes == label and r.priority == "C"),
            total=sum(1 for r in senior if r.lanes == label),
        )
        for label in lane_rank
    )
    ctx.registry.add(
        "people.listed",
        len(rows),
        "in-scope people on the People List",
        f"{len(rows)} in-scope people are listed",
    )
    ctx.registry.add(
        "people.withheld",
        withheld,
        "people recorded for this run without a public profile link",
        f"{withheld} people are not listed (no public profile link)",
    )
    ctx.registry.add(
        "people.excluded_by_title",
        len(excluded),
        "people found whose title the scope excludes",
        f"{len(excluded)} people excluded by title",
    )
    ctx.registry.add(
        "people.senior",
        len(senior),
        "people on the People List",
        f"{len(senior)} listed people meet the seniority rule",
    )
    for i, line in enumerate(summary):
        ctx.registry.add(
            f"people.summary.{i:03d}",
            line.count,
            f"{line.status} {line.reason}".strip(),
            f"{line.count} {line.status} {line.reason}".strip(),
        )
    for i, level in enumerate(levels):
        base = "people on the People List"
        ctx.registry.add(
            f"people.level.{i:02d}", level.people, base, f"{level.people} at {level.label}"
        )
        ctx.registry.add(
            f"people.level.{i:02d}.a",
            level.priority_a,
            base,
            f"{level.priority_a} priority A at {level.label}",
        )
    for i, lane in enumerate(lanes_table):
        base = "listed people who meet the seniority rule"
        for letter, n in (("a", lane.a), ("b", lane.b), ("c", lane.c), ("total", lane.total)):
            ctx.registry.add(
                f"people.lane.{i:02d}.{letter}", n, base, f"{n} {letter.upper()} in {lane.label}"
            )
    for i, s in enumerate(sentences):
        ctx.registry.add_sentence(f"people.sentence.{i:03d}", s)
    attribution = ctx.rules.attribution.name
    what = " ".join(
        x
        for x in (
            texts.what.format(account=c.account_name),
            _merges(c, texts, by_origin, ctx),
            texts.dedupe.format(attribution=attribution, date=_long_date(c.as_of)),
        )
        if x
    )
    what = ctx.rendered(what, where)
    removed = _removed(summary, withheld, texts)
    seniority = (
        f"{texts.seniority_prefix}{ctx.cfg.scope.seniority_rule.rstrip('.')}."
        f" {texts.seniority_filter}"
    )
    exclusion_lines = _exclusions(excluded, texts, ctx.rules.limits.excel_cell_chars)
    paragraphs = tuple(
        x for x in (what, removed, *exclusion_lines, seniority, texts.judgment_note) if x
    )
    model = PeopleModel(
        account=c.account_name,
        as_of=c.as_of,
        rows=tuple(rows),
        summary=summary,
        sentences=sentences,
        in_scope_listed=len(rows),
        title_line=ctx.rendered(texts.title.format(account=c.account_name), where),
        paragraphs=paragraphs,
        what=what,
        seniority_text=seniority,
        judgment_note=texts.judgment_note,
        excluded=excluded,
        levels=levels,
        senior_count=len(senior),
        lanes_table=lanes_table,
    )
    return Mapped(
        model,
        row_ids=tuple(r.row_id for r in rows),
        digests=tuple(digest(r) for r in rows),
        refs=tuple(r.ref for r in rows),
        cited=frozenset(r.source_code for r in rows),
    )
