"""Sources and Evidence mapping (pure), parts A to I of the owner's specification (master prompt
v2, 2026-10-24, section 9.2). Runs last: the source register lists EVERY code the other files
cite (B5), each once, in code order, with its link, type, date, how it was retrieved and every
sentence relied on by a rendered row. The other parts are views of rows the other files already
selected (postings read in full, the same by lane platform, people's own posts, board counts, Leads
and their open questions, data pull counts) so nothing is cited here that is not in a file. A
sentence that contains a restricted term is withheld, counted and listed; the link stays and the
quote is never altered (D-24). No people list is printed here."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import date
from urllib.parse import urlparse

from account_intelligence.grading.product import display_label
from exports.citations import unknown_codes
from exports.content import FactRow, PostingRow
from exports.models import (
    BoardCaptureRow,
    DataPullRow,
    DetectionPullRow,
    EntitySplitRow,
    EvidenceModel,
    EvidenceSource,
    EvidenceUse,
    ExtractQuote,
    ExtractSource,
    JobRead,
    JobRow,
    JobsModel,
    LaneReadGroup,
    LaneReadRow,
    LeadRow,
    LivePostingRow,
    NewsItem,
    PeopleModel,
    PlatformHiringRow,
    SummaryModel,
    TechModel,
    UrlRow,
)
from exports.readers.common import MapContext, Mapped, digest, host_of, ref
from exports.readers.jobs import names_lane_platform
from exports.readers.tech import names_technology
from exports.workspace import account_slug

# origin words (``fact_evidence.origin``) in the order that decides the Retrieved cell
_RETRIEVED = (
    ("full_page_read", "retrieved_live"),
    ("cached_copy", "retrieved_cached"),
    ("feed_item", "retrieved_feed"),
    ("broker_record", "retrieved_feed"),
    ("search_snippet", "retrieved_snippet"),
)


def _code_number(code: str) -> int:
    return int(code.split("-", 1)[1])


def _retrieved(ctx: MapContext, origins: set[str]) -> str:
    labels = ctx.rules.documents.labels
    for origin, label in _RETRIEVED:
        if origin in origins:
            return str(getattr(labels, label))
    return str(labels.retrieved_link)


def _summary_file(ctx: MapContext) -> str:
    """The Account Summary's delivered file name (``output_spec.yaml``), named in the opening
    line as the reference does ("Companion to BD_Account_Summary.docx")."""
    name = ctx.cfg.filenames.get("summary")
    if not name:
        return ""
    return name.replace("{account}", account_slug(ctx.content.account_name)) + ".docx"


def _doc_title(ctx: MapContext) -> str:
    """The title as the reference writes it: "{account} ({legal name}): Sources and Evidence",
    with the Summary's title words (``summary_title``); the legal name comes from the stored
    entity lock when it says more than the account name. Blank when no file name is set."""
    pattern = ctx.cfg.filenames.get("evidence")
    if not pattern:
        return ""
    document = pattern.replace("{account}", "").strip("_ ").replace("_", " ")
    labels = ctx.rules.documents.labels
    account = ctx.content.account_name
    legal = str((ctx.content.entity_lock or {}).get("legal_name") or "").strip()
    if legal and legal.casefold() != account.strip().casefold():
        return str(
            labels.summary_title.format(
                account=account, legal_name=ctx.rendered(legal, "evidence"), document=document
            )
        )
    return str(labels.summary_title_plain.format(account=account, document=document))


def _on_host(where: str, host: str | None) -> bool:
    if not host:
        return False
    host = host.lower().removeprefix("www.")
    return where == host or where.endswith("." + host)


def _source_type(ctx: MapContext, url: str, kind: str, is_posting: bool) -> str:
    """Part A Type, from what the link itself is: a job posting the Job Postings workbook
    lists, the account's own site or careers host, a public profile or a public post. An
    aggregator keeps its labelled kind; anything else returns "" (the caller prints the kind)."""
    if kind == "aggregator":
        return ""
    labels = ctx.rules.documents.labels
    rules = ctx.rules.evidence
    if is_posting:
        return str(labels.source_type_posting)
    parsed = urlparse(url)
    where = (parsed.hostname or "").lower()
    if any(_on_host(where, h) for h in rules.profile_hosts):
        path = parsed.path.lower()
        if any(path.startswith(p) for p in rules.post_paths):
            return str(labels.source_type_post)
        if any(path.startswith(p) for p in rules.profile_paths):
            return str(labels.source_type_profile)
        return ""
    c = ctx.content
    if _on_host(where, c.domain) or _on_host(where, c.careers_host):
        return str(labels.source_type_site)
    return ""


def _read_order(ctx: MapContext, j: JobRow) -> tuple[int, int]:
    """Part B order, as the reference lists its reads: build and admin and owner roles first,
    then working tool, user role and the rest; newest first seen within each group, undated
    last (the sort is stable, so the workbook order breaks ties)."""
    classes = ctx.rules.labels.posting_class
    group = {
        classes["build"]: 0,
        classes["admin_owner"]: 0,
        classes["working_tool"]: 1,
        classes["user"]: 2,
    }.get(j.posting_class, 3)
    seen = j.first_seen or j.posted_or_seen or j.posted_at
    return group, -seen.toordinal() if seen else 1


def _reasoning(ctx: MapContext, p: PostingRow | None, platform: str) -> str:
    """Why a posting has its class: the sentence the class decision stored as its evidence,
    else the posting's platform sentences that name ``platform`` (all of them when none does)."""
    if p is None:
        return ""
    if p.class_sentence.strip():
        return ctx.rendered(p.class_sentence, "evidence")
    key = platform.casefold()
    naming = [s for s in p.platform_sentences if key in s.casefold()] or list(p.platform_sentences)
    return ctx.rendered("\n".join(naming), "evidence") if naming else ""


def _archive(p: PostingRow | None) -> str:
    """The posting's archived copy address, when one is stored."""
    return p.archive_url if p is not None else ""


def _lane_reads(ctx: MapContext, jobs: JobsModel | None) -> list[LaneReadGroup]:
    """Evidence part C, the reference's "Postings read one by one, by lane": one group per
    platform of the Job Postings matrix (``workbooks.jobs.matrix_platforms``) that a posting
    names, holding the postings read in full that name it, in workbook order, with the class,
    the status and the classification reasoning. The heading counts are the workbook's rows
    (the matrix rule: the platform name inside the Platforms named cell, any case)."""
    if jobs is None or not jobs.rows:
        return []  # no postings at all: Part C says "None found" rather than eight empty lanes
    c = ctx.content
    rules = ctx.rules.evidence
    labels = ctx.rules.documents.labels
    open_word = ctx.rules.labels.posting_status["open"]
    on_date = f"{c.as_of.day} {c.as_of:%b %Y}"
    postings = {p.posting_key: p for p in c.postings}
    groups: list[LaneReadGroup] = []
    n = 0
    for i, name in enumerate(ctx.rules.workbooks.jobs.matrix_platforms):
        # a lane no posting names keeps its heading ("0 postings read in full"), as the
        # reference prints every lane in its fixed order
        named = [r for r in jobs.rows if name.casefold() in r.platforms.casefold()]
        rows: list[LaneReadRow] = []
        for r in (r for r in named if r.full_text_read):
            n += 1
            rows.append(
                LaneReadRow(
                    ref=ref("LP", n),
                    row_id=f"lane:{name}:{r.row_id}",
                    posting_ref=r.ref,
                    title=r.title,
                    url=r.url,
                    first_seen=r.posted_or_seen.isoformat()
                    if r.posted_or_seen
                    else labels.not_dated,
                    posting_class=r.posting_class,
                    status=r.status,
                    reasoning=_reasoning(ctx, postings.get(r.row_id), name) or labels.no_sentence,
                    source_code=r.source_code,
                    archive_url=_archive(postings.get(r.row_id)),
                )
            )
        open_now = sum(1 for r in named if r.status == open_word)
        base = f"postings naming {name} on the {ctx.rules.workbooks.jobs.sheet} sheet"
        ctx.registry.add(f"evidence.lane.{i:02d}.named", len(named), base, f"{len(named)} {base}")
        ctx.registry.add(
            f"evidence.lane.{i:02d}.read", len(rows), base, f"{len(rows)} read in full"
        )
        ctx.registry.add(f"evidence.lane.{i:02d}.open", open_now, base, f"{open_now} open")
        groups.append(
            LaneReadGroup(
                platform=name,
                heading=rules.lane_read_heading.format(
                    platform=name,
                    read=len(rows),
                    named=len(named),
                    account=c.account_name,
                    open=open_now,
                    date=on_date,
                ),
                read=len(rows),
                named=len(named),
                open_now=open_now,
                rows=tuple(rows),
            )
        )
    return groups


_EXTRACTS = "evidence.extracts"


def _day(d: date) -> str:
    """A date as the reference file writes it in a Part D sub-heading: 6 Aug 2026."""
    return f"{d.day} {d:%b} {d.year}"


def _extract_heading(ctx: MapContext, code: str, voice: bool) -> str:
    """The Part D sub-heading "<name> (<date>) (<code>)": the stored page title, else the stored
    publisher, else the host (the registry keeps no other name and none is invented); a source
    the Summary quotes an executive from reads "<name> (<date>), executive voice (<code>)"."""
    s = ctx.index.by_code[code]
    stored = (s.title or "").strip() or (s.publisher or "").strip()
    name = ctx.rendered(stored, _EXTRACTS) if stored else host_of(s.url).removeprefix("www.")
    dated = f" ({_day(s.published)})" if s.published else ""
    if voice:
        dated = f"{dated}, {ctx.rules.documents.labels.extract_voice}"
    return f"{name}{dated} ({code})"


def _extracts(
    ctx: MapContext,
    registered: frozenset[str],
    uses_by_code: dict[str, list[EvidenceUse]],
    facts: dict[str, FactRow],
    summary: SummaryModel | None,
) -> tuple[ExtractSource, ...]:
    """Evidence part D, "Video, article and release extracts": one group per source read in
    depth, in register order, holding the executive quotes the Summary prints from it and the
    sentences relied on from its full-page read (each with the grade and the finding it
    supports, one sentence once); then the listed people's own posts the Summary prints, each
    citing its post. Every quote is the text already screened by ``ctx.quote`` for the Summary or
    for part A (never altered, never counted twice); every code is in the register."""
    labels = ctx.rules.documents.labels
    withheld_note = ctx.rules.quote_handling.withheld_text
    evidence = {e.id: e for e in ctx.content.evidence}
    quotes: dict[str, list[ExtractQuote]] = defaultdict(list)
    voiced: set[str] = set()
    for q in summary.executive_voice if summary is not None else ():
        code = next((x for x in q.codes if x in registered), None)
        if code is None:
            continue
        voiced.add(code)
        quotes[code].append(
            ExtractQuote(
                row_id=f"voice:{q.row_id}",
                text=q.text,
                withheld=q.text == withheld_note,
                code=code,
                speaker=q.speaker,
            )
        )
    for code, uses in uses_by_code.items():
        if code not in registered:
            continue
        merged: dict[str, tuple[EvidenceUse, list[str], list[str]]] = {}
        for u in uses:
            ev = evidence.get(u.row_id)
            if ev is None or ev.origin != "full_page_read":
                continue
            fact = facts.get(ev.fact_id)
            key = u.row_id if u.withheld else u.sentence
            _, grades, supports = merged.setdefault(key, (u, [], []))
            grades.append(u.grade_label)
            if fact is not None:
                supports.append(ctx.rendered(fact.value_text, _EXTRACTS))
        for u, grades, supports in merged.values():
            quotes[code].append(
                ExtractQuote(
                    row_id=f"read:{u.row_id}",
                    text=u.sentence,
                    withheld=u.withheld,
                    code=code,
                    grade_label="; ".join(dict.fromkeys(grades)),
                    supports="; ".join(dict.fromkeys(x for x in supports if x)) or None,
                )
            )
    groups = [
        ExtractSource(
            row_id=f"extract:{code}",
            heading=_extract_heading(ctx, code, code in voiced),
            url=ctx.index.by_code[code].url,
            code=code,
            quotes=tuple(quotes[code]),
        )
        for code in sorted(quotes, key=_code_number)
    ]
    posts: list[ExtractQuote] = []
    for f in summary.linkedin_findings if summary is not None else ():
        if f.source_code not in registered:
            continue
        published = ctx.index.by_code[f.source_code].published
        posts.append(
            ExtractQuote(
                row_id=f"post:{f.row_id}",
                text=f.sentence,
                withheld=f.withheld,
                code=f.source_code,
                speaker=f.person,
                supports=f.claim,
                kind=labels.post_type,
                dated=_day(published) if published else None,
            )
        )
    if posts:
        groups.append(
            ExtractSource(row_id="extract:posts", heading=labels.extract_posts, quotes=tuple(posts))
        )
    return tuple(groups)


# ---- parts E, F and H in the reference file's shape -----------------------------------------

# the news feed's ledger operation (``config/grading_policy.yaml`` origin_by_operation)
_NEWS_OPERATION = "news_events"


def _long_day(d: date) -> str:
    """A date as the reference writes it in a Part E sub-heading: 19 October 2026."""
    return f"{d.day} {d:%B} {d.year}"


def _live_postings(
    ctx: MapContext, jobs: JobsModel | None
) -> tuple[str | None, tuple[LivePostingRow, ...]]:
    """Evidence part E, "Live postings captured on <date> that name a lane platform": the Job
    Postings rows the live careers board supplied that name a lane platform, in workbook order,
    with the platforms named, the entity, how far each was read and its class. The sub-heading
    date is the rows' first seen date (a range when they differ); ``None`` without the jobs file."""
    if jobs is None:
        return None, ()
    c = ctx.content
    labels = ctx.rules.documents.labels
    h = ctx.rules.headings.evidence
    postings = {p.posting_key: p for p in c.postings}
    rows: list[LivePostingRow] = []
    seen: list[date] = []
    for j in jobs.rows:
        p = postings.get(j.row_id)
        if p is None or p.origin != "live_board" or not names_lane_platform(p, ctx.cfg.platforms):
            continue
        if p.full_text_read:
            read = labels.read_in_full_yes
        elif p.platform_sentences:
            read = labels.read_platform_sentence
        else:
            read = labels.read_title_only
        if p.first_seen:
            seen.append(p.first_seen)
        rows.append(
            LivePostingRow(
                ref=ref("LV", len(rows) + 1),
                row_id=f"live:{j.row_id}",
                posting=j.title,
                platforms=j.platforms,
                entity=j.entity or c.account_name,
                read=read,
                posting_class=j.posting_class,
                url=j.url,
                archive_url=p.archive_url,
            )
        )
    ctx.registry.add(
        "evidence.live.rows",
        len(rows),
        f"postings on the {ctx.rules.workbooks.jobs.sheet} sheet",
        f"{len(rows)} live board postings name a lane platform",
    )
    if not seen:
        return h.live_postings_undated, tuple(rows)
    first, last = min(seen), max(seen)
    dates = _long_day(first) if first == last else f"{_long_day(first)} to {_long_day(last)}"
    return h.live_postings.format(dates=dates), tuple(rows)


def _lead_queries(ctx: MapContext, status: str | None) -> str:
    """Part F "Queries and checks tried": what the Lead's stored verification status says was
    tried, with the Lead rule numbers of ``config/grading.yaml``; never re-searched when none."""
    labels = ctx.rules.documents.labels
    if not status:
        return str(labels.queries_not_tried)
    text = labels.lead_queries.get(status)
    if text is None:  # a status outside the stored vocabulary prints as stored
        return ctx.rendered(status, "evidence")
    rule = ctx.grading.lead_rule
    return str(
        text.format(queries=rule.min_distinct_queries, tools=rule.min_distinct_tool_categories)
    )


def _detections(ctx: MapContext, tech: TechModel | None) -> tuple[DetectionPullRow, ...]:
    """Part H "Technology detections for lane platforms": the Tech Stack workbook's detection
    rows a lane platform of ``config/platform_dictionary.yaml`` names, with first and last seen
    and the postings of the Job Postings file naming the technology (the workbook's own cross
    check count). The detection feed's score and its postings and pages split are not stored."""
    if tech is None:
        return ()
    lane = [e for e in ctx.cfg.platforms if e.lane]
    postings = ctx.content.postings
    rows: list[DetectionPullRow] = []
    for t in tech.rows:
        if not any(e.names(t.technology) for e in lane):
            continue
        rows.append(
            DetectionPullRow(
                ref=ref("TD", len(rows) + 1),
                row_id=f"detection:{t.row_id}",
                technology=t.technology,
                first_seen=t.first_seen,
                last_seen=t.last_seen,
                postings=sum(1 for p in postings if names_technology(p, t.technology)),
            )
        )
    return tuple(rows)


def _platform_postings(ctx: MapContext, jobs: JobsModel | None) -> tuple[PlatformHiringRow, ...]:
    """Part H "Postings naming each lane platform": the Job Postings workbook's platform by
    class matrix (its own counts, registered there) and the open postings naming each platform,
    one row per platform named at least once."""
    if jobs is None or not jobs.class_labels:
        return ()
    classes = ctx.rules.labels.posting_class
    index = {label: i for i, label in enumerate(jobs.class_labels)}
    open_word = ctx.rules.labels.posting_status["open"]
    all_rows = ctx.rules.workbooks.jobs.matrix_all_rows

    def by(counts: tuple[int, ...], key: str) -> int:
        i = index.get(classes.get(key, ""))
        return counts[i] if i is not None else 0

    out: list[PlatformHiringRow] = []
    for m in jobs.matrix:
        if m.platform == all_rows or m.rows == 0:
            continue
        named = [r for r in jobs.rows if m.platform.casefold() in r.platforms.casefold()]
        out.append(
            PlatformHiringRow(
                ref=ref("PP", len(out) + 1),
                row_id=f"platform:{m.platform}",
                platform=m.platform,
                postings=m.rows,
                open_now=sum(1 for r in named if r.status == open_word),
                build=by(m.by_class, "build"),
                admin_owner=by(m.by_class, "admin_owner"),
                working_tool=by(m.by_class, "working_tool"),
                user=by(m.by_class, "user"),
            )
        )
    return tuple(out)


def _entity_split(ctx: MapContext, jobs: JobsModel | None) -> tuple[EntitySplitRow, ...]:
    """Part H "Entity split": the Job Postings rows per entity cell, most postings first."""
    if jobs is None:
        return ()
    counts = Counter(r.entity or ctx.content.account_name for r in jobs.rows)
    rows = tuple(
        EntitySplitRow(ref=ref("ES", i), row_id=f"entity:{name}", entity=name, postings=n)
        for i, (name, n) in enumerate(sorted(counts.items(), key=lambda x: (-x[1], x[0])), 1)
    )
    sheet = ctx.rules.workbooks.jobs.sheet
    for i, r in enumerate(rows):
        ctx.registry.add(
            f"evidence.entity.{i:03d}",
            r.postings,
            f"postings on the {sheet} sheet",
            f"{r.postings} postings for {r.entity}",
        )
    return rows


def _news_feed_note(ctx: MapContext) -> str:
    """Part H "News feed events": the feed's events are not part of the stored snapshot, so the
    note says how many the run's ledger recorded the feed returning, and where the findings
    taken from it are (the register, retrieved as Feed)."""
    labels = ctx.rules.documents.labels
    calls = [p for p in ctx.content.pull_calls if p.operation == _NEWS_OPERATION]
    if not calls:
        return str(labels.news_feed_none)
    feed = labels.retrieved_feed
    if any(p.result_count is None for p in calls):
        note = labels.news_feed_uncounted.format(calls=len(calls), feed=feed)
    else:
        events = sum(p.result_count or 0 for p in calls)
        note = labels.news_feed_counted.format(events=events, calls=len(calls), feed=feed)
    ctx.registry.add_sentence("evidence.news_feed", note)
    return str(note)


def map_evidence(
    ctx: MapContext,
    cited_by_files: Iterable[str],
    rendered_fact_ids: frozenset[str],
    jobs: JobsModel | None,
    tech: TechModel | None = None,
    people: PeopleModel | None = None,
    summary: SummaryModel | None = None,
) -> Mapped[EvidenceModel]:
    """The Evidence render model over the union of cited codes and rendered facts' sources."""
    c = ctx.content
    labels = ctx.rules.documents.labels
    where = "evidence"
    facts = {f.id: f for f in c.facts if f.id in rendered_fact_ids}
    uses_by_code: dict[str, list[EvidenceUse]] = defaultdict(list)
    origins_by_code: dict[str, set[str]] = defaultdict(set)
    codes_by_fact: dict[str, set[str]] = defaultdict(set)
    finding_of: dict[str, str] = {}
    n_uses = 0
    for ev in sorted(c.evidence, key=lambda e: (e.fact_id, e.id)):
        if ev.evidence_kind == "search_proof" or ctx.denied(ev.source_code, ev.url):
            continue  # a link naming a data tool is withheld by the Summary mapper
        code = ev.source_code or ctx.index.code_for(ev.url)
        if code is None:
            continue  # recorded as missing_source_code by the Summary mapper
        if ev.origin:
            origins_by_code[code].add(ev.origin)
        fact = facts.get(ev.fact_id)
        if fact is None:
            continue
        codes_by_fact[fact.id].add(code)
        if not ctx.printable(ev.supporting_sentence, where, ev.id):
            continue
        n_uses += 1
        sentence, withheld = ctx.quote(ev.supporting_sentence, where)
        flags = ("aggregator",) if ev.is_aggregator else ()
        if fact.id not in finding_of:  # rendered once per fact (the leak count is per text)
            finding_of[fact.id] = ctx.rendered(fact.value_text, where).strip()
        uses_by_code[code].append(
            EvidenceUse(
                row_id=ev.id,
                grade_label=display_label(
                    fact.product_grade,  # type: ignore[arg-type]
                    tuple(sorted({*fact.flags, *flags})),
                    fact.lead_status,
                ),
                sentence=sentence,
                withheld=withheld,
                finding=finding_of[fact.id],
                fact_id=fact.id,
            )
        )
    if ctx.capped(n_uses, ctx.rules.limits.row_caps.evidence_uses, where):
        return Mapped(None)
    codes = (set(cited_by_files) | set(uses_by_code)) - ctx.denied_codes
    ctx.flag(unknown_codes(codes, ctx.index, where))
    known = sorted((x for x in codes if ctx.index.knows(x)), key=_code_number)
    kind_labels = ctx.rules.labels.source_kind
    posting_of = {j.source_code: j for j in jobs.rows} if jobs is not None else {}
    posting_codes = set(posting_of)
    sources: list[EvidenceSource] = []
    for i, code in enumerate(known, start=1):
        s = ctx.index.by_code[code]
        if s.kind not in kind_labels:
            ctx.fail("source_kind_vocabulary", where, code)
            continue
        sources.append(
            EvidenceSource(
                ref=ref("E", i),
                code=code,
                url=s.url,
                published=s.published,
                kind_label=kind_labels[s.kind],
                uses=tuple(uses_by_code.get(code, ())),
                retrieved=_retrieved(ctx, origins_by_code.get(code, set())),
                title=ctx.rendered(s.title, where).strip() if s.title else "",
                type_label=_source_type(ctx, s.url, s.kind, code in posting_codes)
                or kind_labels[s.kind],
                supports_note=labels.posting_supports.format(
                    ref=posting_of[code].ref,
                    title=posting_of[code].title,
                    cls=posting_of[code].posting_class,
                )
                if code in posting_of and not uses_by_code.get(code)
                else "",
                archive_url=s.archive_url,
            )
        )

    # B: postings read in full, with the posting's own platform sentences
    reads: list[JobRead] = []
    if jobs is not None:
        postings = {p.posting_key: p for p in c.postings}
        full = [j for j in jobs.rows if (p := postings.get(j.row_id)) and p.full_text_read]
        for j in sorted(full, key=lambda j: _read_order(ctx, j)):
            p = postings[j.row_id]
            sentences = ctx.rendered("\n".join(p.platform_sentences) or j.platform_sentence, where)
            seen = j.first_seen or j.posted_or_seen or j.posted_at
            reads.append(
                JobRead(
                    ref=ref("JD", len(reads) + 1),
                    row_id=j.row_id,
                    title=j.title,
                    url=j.url,
                    code=j.source_code,
                    posting_ref=j.ref,
                    posted=j.posted_at.isoformat() if j.posted_at else labels.not_dated,
                    entity=j.entity or c.account_name,
                    sentences=sentences,
                    posting_class=j.posting_class,
                    status=j.status,
                    first_seen=seen.isoformat() if seen else "",
                    platforms=j.platforms,
                    archive_url=p.archive_url,
                )
            )

    # C: postings read in full, one table per lane platform
    lane_reads = _lane_reads(ctx, jobs)
    lane_rows = [r for g in lane_reads for r in g.rows]

    # D: video, article and release extracts, one sub-heading per source read in depth (the
    # Summary's executive voice, full-page reads, the listed people's own posts)
    extracts = _extracts(ctx, frozenset(s.code for s in sources), uses_by_code, facts, summary)

    # E: the job board count lines of the jobs pipeline
    captures: list[BoardCaptureRow] = []
    if c.jobs_counts is not None:
        for key, label, value, base in c.jobs_counts.lines:
            shown_label, shown_base = ctx.rendered(label, where), ctx.rendered(base, where)
            captures.append(
                BoardCaptureRow(
                    ref=ref("BC", len(captures) + 1),
                    row_id=f"board:{key}",
                    keyword=shown_label,
                    matches=value,
                    read=shown_base,
                )
            )
            # the registry's texts reach the manifest: the printed (checked) forms, never the stored
            ctx.registry.add(f"evidence.board.{key}", value, shown_base, f"{value} {shown_label}")
    # E: the live board postings that name a lane platform
    live_heading, live = _live_postings(ctx, jobs)

    # F: Leads, what was tried, what would settle them
    leads: list[LeadRow] = []
    for fid in sorted(facts):
        fact = facts[fid]
        if fact.product_grade != "lead" or not codes_by_fact.get(fid):
            continue
        leads.append(
            LeadRow(
                ref=ref("LD", len(leads) + 1),
                row_id=f"lead:{fid}",
                lead=ctx.rendered(fact.value_text, where),
                queries=_lead_queries(ctx, fact.lead_status),
                grade_label=display_label(fact.product_grade, fact.flags, fact.lead_status),  # type: ignore[arg-type]
                settle=ctx.rendered(fact.confirming_question, where),
                codes=tuple(sorted(codes_by_fact[fid], key=_code_number)),
            )
        )

    # H: data pull counts (technology detections)
    pulls: list[DataPullRow] = []
    if tech is not None:
        for line in tech.counts:
            pulls.append(
                DataPullRow(
                    ref=ref("DP", len(pulls) + 1),
                    row_id=f"pull:{line.key}",
                    pull=line.label,
                    count=line.value,
                    base=line.base,
                )
            )
    # H: detections of lane platforms, postings per lane platform, the entity split, the news
    # feed note and the teams note
    detections = _detections(ctx, tech)
    platform_postings = _platform_postings(ctx, jobs)
    entity_split = _entity_split(ctx, jobs)
    news_feed_note = _news_feed_note(ctx)

    # I: every URL of the register, written out
    urls = tuple(
        UrlRow(ref=ref("U", i), code=s.code, url=s.url, archive_url=s.archive_url)
        for i, s in enumerate(sources, 1)
    )

    prefixes = ctx.rules.evidence.news_attribute_prefixes
    news: list[NewsItem] = []
    for fid in sorted(facts):
        fact = facts[fid]
        if not any(fact.attribute_key.startswith(p) for p in prefixes):
            continue
        for ev in sorted(c.evidence, key=lambda e: e.id):
            code = ev.source_code or ctx.index.code_for(ev.url)
            if ev.fact_id != fid or not ev.url or code is None or ctx.denied(code, ev.url):
                continue
            sentence, _ = ctx.quote(ev.supporting_sentence, where)
            news.append(
                NewsItem(
                    ref=ref("N", len(news) + 1),
                    row_id=ev.id,
                    sentence=sentence,
                    url=ev.url,
                    code=code,
                    verified=ev.origin == "full_page_read",
                )
            )
    ctx.registry.add(
        "evidence.sources",
        len(sources),
        "sources cited by this file set",
        f"{len(sources)} sources",
    )
    ctx.registry.add(
        "evidence.withheld",
        ctx.withheld,
        "sentences relied on in this file set",
        f"{ctx.withheld} sentences withheld",
    )
    ctx.registry.add("evidence.leads", len(leads), "findings graded Lead", f"{len(leads)} Leads")
    model = EvidenceModel(
        brand=ctx.rendered(c.brand_name, "branding") if c.brand_name else None,
        account=c.account_name,
        as_of=c.as_of,
        summary_file=_summary_file(ctx),
        doc_title=_doc_title(ctx),
        sources=tuple(sources),
        withheld=sum(1 for s in sources for u in s.uses if u.withheld),
        job_descriptions=tuple(reads),
        lane_reads=tuple(lane_reads),
        extracts=extracts,
        board_captures=tuple(captures),
        leads=tuple(leads),
        keep_copies=labels.keep_copies_text,
        data_pulls=tuple(pulls),
        urls=urls,
        news=tuple(news),
        none_found=ctx.rules.none_found.text,
        live_heading=live_heading,
        live_postings=live,
        detections=detections,
        platform_postings=platform_postings,
        entity_split=entity_split,
        news_feed_note=news_feed_note,
        teams_note=tech.teams_note if tech is not None else None,
    )
    # rows in document order (parts A, B, C, E, F, H, I); the refs must match the keys
    # re-read from the produced file (B3). Part D prints quotes under sub-headings, no table
    # rows: its sources are the register's rows and its quotes part A's or the Summary's
    keyed = [(s.code, s.ref, digest(s)) for s in sources]
    keyed += [(j.row_id, j.ref, digest(j)) for j in reads]
    keyed += [(r.row_id, r.ref, digest(r)) for r in lane_rows]
    keyed += [(r.row_id, r.ref, digest(r)) for r in captures]
    keyed += [(r.row_id, r.ref, digest(r)) for r in live]
    keyed += [(r.row_id, r.ref, digest(r)) for r in leads]
    keyed += [(r.row_id, r.ref, digest(r)) for r in pulls]
    keyed += [(r.row_id, r.ref, digest(r)) for r in detections]
    keyed += [(r.row_id, r.ref, digest(r)) for r in platform_postings]
    keyed += [(r.row_id, r.ref, digest(r)) for r in entity_split]
    # Part I is plain lines (one per code), as the reference prints it: its rows carry no
    # visible key; the URL itself is the register's and is verified there
    keyed += [(f"url:{r.code}", "", digest(r)) for r in urls]
    return Mapped(
        model,
        row_ids=tuple(k[0] for k in keyed),
        digests=tuple(k[2] for k in keyed),
        refs=tuple(k[1] for k in keyed if k[1]),
        cited=frozenset(s.code for s in sources),
    )
