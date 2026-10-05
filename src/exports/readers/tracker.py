"""Coverage tracker mapping (pure). Rows and tally come from the tracker's client view
(``tracker.render.render_client_view`` and ``tracker.tally``, run by the reader) in the tracker's
own order (most valuable open work first); the builder never types a status or a count."""

from __future__ import annotations

from exports.models import TrackerRow
from exports.readers.common import MapContext


def map_tracker(ctx: MapContext) -> tuple[tuple[TrackerRow, ...], tuple[str, ...]]:
    """Tracker rows (refs ``T-0001``..) and tally sentences; registers the tally (B6)."""
    c = ctx.content
    where = "summary.tracker"
    if not c.tracker or not c.tracker_tally:
        ctx.fail("missing_tracker", where, "tracker client view")
        return (), ()
    if ctx.capped(len(c.tracker), ctx.rules.limits.row_caps.tracker_rows, where):
        return (), ()
    rows = tuple(
        TrackerRow(
            ref=f"T-{i:04d}",
            row_id=t.key,
            row=ctx.rendered(t.row, where),
            status=ctx.rendered(t.status, where),
            priority=ctx.rendered(t.priority, where),
            reason=ctx.rendered(t.reason, where) if t.reason else None,
            tools=ctx.rendered(", ".join(t.tools), where),
            results="Not counted" if t.results is None else str(t.results),
        )
        for i, t in enumerate(c.tracker, start=1)
    )
    tally = tuple(ctx.rendered(s, where) for s in c.tracker_tally)
    ctx.registry.add(
        "tracker.rows", len(rows), "coverage rows of this run", f"{len(rows)} coverage rows"
    )
    for i, s in enumerate(tally):
        ctx.registry.add_sentence(f"tracker.tally.{i:02d}", s)
    for i, t in enumerate(c.tracker):
        if t.results is not None:
            ctx.registry.add(
                f"tracker.results.{i:03d}",
                t.results,
                f"results of the searches for {t.key}",
                f"{t.results} results",
            )
    return rows, tally
