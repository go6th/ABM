"""Coverage tracker mapping (pure). Rows come from the tracker's client view
(``tracker.render.render_client_view``, run by the reader), pass the section 11 consistency rules
(``tracker.consistency``) against the facts beside them, and are printed pass by pass, High
first and within a priority the rows that need nothing before the rows waiting on someone
(master prompt v2 9.1 item 11). The tally is recomputed from the rows after the rules; the
builder never types a status or a count."""

from __future__ import annotations

from account_intelligence.tracker.config import load_checklist, load_tracker_config
from account_intelligence.tracker.consistency import (
    ContentSignals,
    CoverageLine,
    apply_rules,
    tally_sentences,
)
from account_intelligence.tracker.render import STATUS_TEXT
from exports.models import TrackerRow
from exports.readers.common import MapContext, months_before, parse_event_date

_PRIORITY_RANK = {"High": 0, "Medium": 1, "Optional": 2}
SIGNAL_TIERS = (1, 2, 3)


def content_signals(ctx: MapContext) -> ContentSignals:
    """What the content says that a tracker row may not contradict: dated signal facts (news
    rows), a leadership change inside the configured window, the account's listing status."""
    rules = load_tracker_config().consistency
    leadership_rows = set(rules.get("leadership_attribute_rows") or ())
    cutoff = months_before(ctx.content.as_of, int(rules.get("leadership_months") or 6))
    dated_news = recent_change = False
    for f in ctx.content.facts:
        if f.visibility != "public" or f.claim_kind == "absence":
            continue
        spec = ctx.attribute(f.attribute_key)
        when = parse_event_date(f.event_date)
        if spec is None or when is None:
            continue
        if spec.tier in SIGNAL_TIERS:
            dated_news = True
        if spec.row_key in leadership_rows and when >= cutoff:
            recent_change = True
    return ContentSignals(
        dated_news=dated_news,
        recent_leadership_change=recent_change,
        is_public=ctx.content.is_public,
        reference_list_supplied=False,
    )


def _done(ctx: MapContext, line: CoverageLine, tools: str) -> str:
    labels = ctx.rules.documents.labels
    if not tools:
        # "Not started" only where the row is still open; a finished row without a recorded
        # search says so instead of claiming nothing was done
        open_words = (STATUS_TEXT["blocked"], STATUS_TEXT["not_done"], STATUS_TEXT["unset"])
        return str(labels.done_nothing if line.status in open_words else labels.done_unrecorded)
    if line.results is None:
        return str(labels.done_uncounted.format(tools=tools))
    if line.results == 0:
        return str(labels.done_zero.format(tools=tools))
    return str(labels.done_results.format(n=line.results, tools=tools))


def tally_line(ctx: MapContext, rows: tuple[TrackerRow, ...]) -> str | None:
    """The one tally line the document prints under the two tables, as the reference words it:
    the row count and each status with its count, in the order the statuses first appear in the
    printed rows. Computed from the rows after the consistency rules and registered (B6)."""
    if not rows:
        return None
    counts: dict[str, int] = {}
    for t in rows:
        counts[t.status] = counts.get(t.status, 0) + 1
    line: str = ctx.rules.documents.labels.tally_line.format(
        n=len(rows), counts=", ".join(f"{status} {n}" for status, n in counts.items())
    )
    ctx.registry.add_sentence("tracker.tally_line", line)
    return line


def map_tracker(ctx: MapContext) -> tuple[tuple[TrackerRow, ...], tuple[str, ...]]:
    """Tracker rows (refs ``T-0001``.. in document order) and tally sentences; registers the
    tally and every result count (B6)."""
    c = ctx.content
    where = "summary.tracker"
    if not c.tracker or not c.tracker_tally:
        ctx.fail("missing_tracker", where, "tracker client view")
        return (), ()
    if ctx.capped(len(c.tracker), ctx.rules.limits.row_caps.tracker_rows, where):
        return (), ()
    lines = tuple(
        CoverageLine(
            key=t.key,
            row=t.row,
            status=t.status,
            priority=t.priority,
            reason=t.reason,
            tools=tuple(t.tools),
            results=t.results,
        )
        for t in c.tracker
    )
    adjusted = apply_rules(lines, content_signals(ctx))
    tally = tuple(ctx.rendered(s, where) for s in tally_sentences(adjusted))
    passes = {r.key: r.pass_no for r in load_checklist().rows}
    labels = ctx.rules.documents.labels
    ordered = sorted(
        enumerate(adjusted),
        key=lambda it: (
            passes.get(it[1].key, 2),
            _PRIORITY_RANK.get(it[1].priority, len(_PRIORITY_RANK)),
            1 if it[1].reason else 0,
            it[0],
        ),
    )
    rows: list[TrackerRow] = []
    for i, (_, t) in enumerate(ordered, start=1):
        tools = ctx.rendered(", ".join(t.tools), where)
        reason = ctx.rendered(t.reason, where) if t.reason else None
        rows.append(
            TrackerRow(
                ref=f"T-{i:04d}",
                row_id=t.key,
                row=ctx.rendered(t.row, where),
                status=ctx.rendered(t.status, where),
                priority=ctx.rendered(t.priority, where),
                reason=reason,
                tools=tools,
                results="Not counted" if t.results is None else str(t.results),
                needs=reason or labels.needs_nothing,
                done=_done(ctx, t, tools),
                remains=labels.remains.get(t.status, ""),
                pass_no=passes.get(t.key, 2),
            )
        )
    ctx.registry.add(
        "tracker.rows", len(rows), "coverage rows of this run", f"{len(rows)} coverage rows"
    )
    for i, s in enumerate(tally):
        ctx.registry.add_sentence(f"tracker.tally.{i:02d}", s)
    for i, (_, t) in enumerate(ordered):
        if t.results is not None:
            ctx.registry.add(
                f"tracker.results.{i:03d}",
                t.results,
                f"results of the searches for {t.key}",
                f"{t.results} results",
            )
    return tuple(rows), tally
