"""People List mapping (pure). The selection and the Summary sheet lines are the people module's
own (``people.export.build_export``, run by the reader); this maps them to the render model in
the shape of the owner's reference People List (reference set of 2026-10-20, measured
2026-10-29): rows ordered by level, priority, lane and name; level, lane, seniority and found-by
labels from ``export_rules.yaml labels``; the Summary sheet's title and its six paragraphs (what
the list merges and from which searches, with each search's counts; the rows removed before it;
the people excluded by title by name; the seniority rule; the judgment note; the entity labels)
built only from the stored people record, the account's stored entity lock and the run's stored
search calls. Every person has ONE lane (the title's role family) and a Found by that names the
search source as the reference prints it. It attaches the registered source code (B4) and
registers every count the Summary sheet renders (B6). The Level and Lane tables are workbook
formulas over the People sheet; their code-computed values live on the model for the
recalculation check (B12)."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date

from exports.config import Labels, PeopleSheetTexts
from exports.content import FoundVia, PersonRow, PullCall, SnapshotContent
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
_PLURAL = {
    "row": "rows",
    "search": "searches",
    "title search": "title searches",
    "record": "records",
    "person": "people",
}
# the order the Found by cell lists the ways a person was found
_ORIGIN_ORDER = {"title_search": 0, "association": 1, "mention": 2}


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


def _lane_order(labels: Labels) -> list[str]:
    """Lane ids in the People List's order (the Lane table's order): ``people_lane``."""
    return [_id(x) for x in labels.people_lane]


def _lane_id(p: PersonRow, labels: Labels) -> str:
    """The one lane id of a person: the stored role lane; for a record stored before the rule
    existed, the lane of its first platform lane (``people_lane_platform``), else the default."""
    stored = _id(p.role_lane)
    if stored in labels.people_lane:
        return stored
    for platform in (_id(x) for x in p.lanes.split(",") if x.strip()):
        mapped = labels.people_lane_platform.get(platform)
        if mapped in labels.people_lane:
            return str(mapped)
    return labels.people_lane_default


def _is_leadership_page(url: str | None, labels: Labels) -> bool:
    """True when a research page's address names a leadership, management or team page."""
    if not url:
        return False
    parts = re.split(r"[^a-z0-9]+", url.casefold())
    return any(w in parts for w in labels.found_by_leadership_words)


def _found_by(p: PersonRow, labels: Labels) -> str:
    """The reference's Found by: the search source of every way the person was found, joined by
    ``; `` (``title search source 1 (leadership and platform)``, ``technology association search
    (Tableau)``, ``Leadership page; profile found by search``)."""
    vias = list(p.found_via) or [FoundVia(origin=p.origin)]
    vias.sort(key=lambda v: (_ORIGIN_ORDER.get(v.origin, 9), v.pass_no or 0))
    parts: list[str] = []
    platforms: list[str] = []
    for v in vias:
        if v.origin == "title_search":
            band = labels.found_by_pass.get(v.pass_no or 0)
            parts.append(
                labels.found_by["title_search"].format(pass_no=v.pass_no, band=band)
                if v.pass_no and band
                else labels.found_by["title_search_any"]
            )
        elif v.origin == "association":
            platform = labels.found_by_platform.get(_id(v.lane))
            if platform:
                platforms.append(platform)
            elif "association_any" not in parts:
                parts.append(labels.found_by["association_any"])
        else:
            key = (
                "mention_leadership"
                if _is_leadership_page(v.mention_url, labels)
                else "mention_other"
            )
            parts.append(labels.found_by[key])
    if platforms:
        parts.append(
            labels.found_by["association"].format(platforms=", ".join(dict.fromkeys(platforms)))
        )
    return "; ".join(dict.fromkeys(parts))


def _pass_of(call: PullCall, labels: Labels) -> int:
    """The title-search pass of a stored call: the pass whose title words (``found_by_pass_terms``)
    the call's titles contain, else pass 1."""
    titles = " ".join((*call.terms, *call.queries)).casefold()
    for pass_no, words in sorted(labels.found_by_pass_terms.items(), reverse=True):
        if any(w in titles for w in words):
            return pass_no
    return 1


def _merges(
    c: SnapshotContent,
    texts: PeopleSheetTexts,
    labels: Labels,
    listed: list[PersonRow],
    ctx: MapContext,
) -> str:
    """ "It merges ..." from the stored search calls and the listed people by how they were
    found, with each title search's own counts; empty when nothing was searched or listed."""
    title_calls = [x for x in c.pull_calls if x.operation == TITLE_SEARCH]
    assoc_calls = [x for x in c.pull_calls if x.operation == ASSOCIATION_SEARCH]
    by_origin: Counter[str] = Counter()
    by_pass: Counter[int] = Counter()
    gap_platforms: list[str] = []
    for p in listed:
        vias = p.found_via or (FoundVia(origin=p.origin),)
        for origin in {v.origin for v in vias}:
            by_origin[origin] += 1
        for pass_no in {v.pass_no for v in vias if v.origin == "title_search" and v.pass_no}:
            by_pass[pass_no] += 1
        for v in vias:
            platform = (
                labels.found_by_platform.get(_id(v.lane)) if v.origin == "association" else None
            )
            if platform and platform not in gap_platforms:
                gap_platforms.append(platform)
    parts: list[str] = []

    def count_words(calls: list[PullCall], people: int) -> str:
        out = []
        counted = [x.result_count for x in calls if x.result_count is not None]
        if counted:
            out.append(texts.matches.format(records=_n(sum(counted), "record")))
        out.append(texts.on_list.format(people=_n(people, "person")))
        return ", ".join(out)

    if title_calls or by_origin["title_search"]:
        domains = sorted({x.domain.casefold() for x in title_calls if x.domain})
        if not domains and c.domain:
            domains = [c.domain.casefold()]
        passes = sorted({_pass_of(x, labels) for x in title_calls} | set(by_pass))
        per_pass = []
        for pass_no in passes:
            calls = [x for x in title_calls if _pass_of(x, labels) == pass_no]
            band = labels.found_by_pass.get(pass_no, str(pass_no))
            per_pass.append(
                texts.search_pass.format(label=band, details=count_words(calls, by_pass[pass_no]))
            )
            counted = [x.result_count for x in calls if x.result_count is not None]
            ctx.registry.add(
                f"people.searches.title.pass{pass_no}.records",
                sum(counted),
                "stored search calls of this run",
                f"{sum(counted)} records loaded by title search source {pass_no}",
            )
            ctx.registry.add(
                f"people.listed.title_search.pass{pass_no}",
                by_pass[pass_no],
                "people on the People List",
                f"{by_pass[pass_no]} found by title search source {pass_no}",
            )
        template = texts.merge_title if domains else texts.merge_title_any
        parts.append(
            template.format(
                searches=_n(len(title_calls), "title search")
                if title_calls
                else texts.searches_unknown,
                domains=_and(domains),
                passes="; ".join(per_pass) or count_words(title_calls, by_origin["title_search"]),
            )
        )
    if assoc_calls or by_origin["association"]:
        details = count_words(assoc_calls, by_origin["association"])
        parts.append(
            texts.merge_association.format(platforms=_and(gap_platforms), details=details)
            if gap_platforms
            else texts.merge_association_any.format(details=details)
        )
    if by_origin["mention"]:
        parts.append(texts.merge_mention.format(details=count_words([], by_origin["mention"])))
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
    and the title exclusions, listed apart, are left out); a sentence saying none when nothing
    was removed (the sheet always has its six notes)."""
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
    return texts.removed.format(items="; ".join(items)) if items else texts.removed_none


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


def _entity_labels(c: SnapshotContent, texts: PeopleSheetTexts) -> str:
    """The entity-labels paragraph: the account's subsidiaries (from the stored entity lock) are
    its own entities and are kept; people of a business the account sold or combined may still
    show the account as employer. Only account data fills it."""
    lock = c.entity_lock or {}
    names = [
        str(x.get("name") or "").strip()
        for x in (lock.get("subsidiaries") or [])
        if isinstance(x, dict) and str(x.get("name") or "").strip()
    ]
    names = list(dict.fromkeys(names))
    if names:
        return texts.entity_labels.format(entities=_and(names), account=c.account_name)
    return texts.entity_labels_none.format(account=c.account_name)


def map_people(ctx: MapContext) -> Mapped[PeopleModel]:
    """People rows ordered as the reference orders them (level, priority, lane, name), refs
    ``P-0001``..; the Summary sheet carries the title, the list's provenance, the rows removed,
    the people excluded by title by name, the seniority rule, the judgment note, the entity
    labels and the Level and Lane tables."""
    c = ctx.content
    where = "people"
    labels = ctx.rules.labels
    texts = ctx.rules.sheet_texts.people
    if ctx.capped(len(c.people), ctx.rules.limits.row_caps.people, where):
        return Mapped(None)
    lane_order = _lane_order(labels)
    lane_labels = list(dict.fromkeys(labels.people_lane[x] for x in lane_order))
    lane_rank = {label: n for n, label in enumerate(lane_labels)}
    level_rank = {label: n for n, label in enumerate(labels.people_level_order)}

    def order(p: PersonRow) -> tuple[int, str, int, str, str]:
        lane = labels.people_lane[_lane_id(p, labels)]
        level = level_rank.get(labels.level_label(p.level), len(level_rank))
        return (
            level,
            p.priority,
            lane_rank.get(lane, len(lane_rank)),
            p.name.casefold(),
            p.profile_url,
        )

    rows: list[PeopleRow] = []
    listed: list[PersonRow] = []
    for i, p in enumerate(sorted(c.people, key=order), start=1):
        code = ctx.code_or_withhold(p.source_code, p.profile_url, where, p.id)
        name = ctx.rendered(p.name, where)
        if not name.strip():
            ctx.fail("name_missing", where, p.id)  # nothing printable once sanitised
            continue
        if code is None:
            continue
        if not p.origin or p.origin not in _ORIGIN_ORDER:
            ctx.fail("origin_vocabulary", where, p.id)
            continue
        lane_id = _lane_id(p, labels)
        # nothing was read for the person: the cell stays empty (no claim either way)
        former = ctx.rendered("; ".join(p.former_employers or ()), where)
        listed.append(p)
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
                lanes=labels.people_lane[lane_id],
                lane_id=lane_id,
                priority=ctx.rendered(p.priority, where),
                former_employers=former,
                found_by=ctx.rendered(_found_by(p, labels), where),
                profile_url=p.profile_url,
                second_profile_url=p.second_profile_url or None,
                location=ctx.rendered(p.location, where) if p.location else "",
                note="",
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
        for label in lane_labels
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
            _merges(c, texts, labels, listed, ctx),
            texts.dedupe.format(attribution=attribution, date=_long_date(c.as_of)),
        )
        if x
    )
    what = ctx.rendered(what, where)
    removed = _removed(summary, withheld, texts)
    seniority = (
        f"{texts.seniority_prefix}{ctx.cfg.scope.seniority_rule.rstrip('.')}."
        f" {texts.seniority_below} {texts.seniority_filter}"
    )
    exclusion_lines = _exclusions(excluded, texts, ctx.rules.limits.excel_cell_chars)
    entity = ctx.rendered(_entity_labels(c, texts), where)
    paragraphs = tuple(
        x for x in (what, removed, *exclusion_lines, seniority, texts.judgment_note, entity) if x
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
