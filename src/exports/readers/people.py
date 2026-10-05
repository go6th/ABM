"""People List mapping (pure). The selection, order and Summary sheet lines are the people
module's own (``people.export.build_export``, run by the reader); this maps them to the render
model, attaches the registered source code (B4) and registers every count (B6)."""

from __future__ import annotations

from exports.models import PeopleModel, PeopleRow, SummaryLine
from exports.readers.common import MapContext, Mapped, digest, ref


def map_people(ctx: MapContext) -> Mapped[PeopleModel]:
    """People rows in the people module's order, refs ``P-0001``..; the Summary sheet counts by
    status and reason (never names)."""
    c = ctx.content
    where = "people"
    if ctx.capped(len(c.people), ctx.rules.limits.row_caps.people, where):
        return Mapped(None)
    rows: list[PeopleRow] = []
    for i, p in enumerate(c.people, start=1):
        code = ctx.code_or_fail(p.source_code, p.profile_url, where, p.id)
        name = ctx.rendered(p.name, where)
        if not name.strip():
            ctx.fail("name_missing", where, p.id)  # nothing printable once sanitised
            continue
        if code is None:
            continue
        rows.append(
            PeopleRow(
                ref=ref("P", i),
                row_id=p.id,
                name=name,
                title=ctx.rendered(p.title, where),
                level=ctx.rendered(p.level, where),
                lanes=ctx.rendered(p.lanes, where),
                priority=ctx.rendered(p.priority, where),
                profile_url=p.profile_url,
                source_code=code,
            )
        )
    if len(rows) != len(c.people):
        return Mapped(None)
    summary: tuple[SummaryLine, ...]
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
    ctx.registry.add(
        "people.listed",
        len(rows),
        "in-scope people on the People List",
        f"{len(rows)} in-scope people are listed",
    )
    for i, line in enumerate(summary):
        ctx.registry.add(
            f"people.summary.{i:03d}",
            line.count,
            f"{line.status} {line.reason}".strip(),
            f"{line.count} {line.status} {line.reason}".strip(),
        )
    for i, s in enumerate(sentences):
        ctx.registry.add_sentence(f"people.sentence.{i:03d}", s)
    model = PeopleModel(
        account=c.account_name,
        as_of=c.as_of,
        rows=tuple(rows),
        summary=summary,
        sentences=sentences,
        in_scope_listed=len(rows),
    )
    return Mapped(
        model,
        row_ids=tuple(r.row_id for r in rows),
        digests=tuple(digest(r) for r in rows),
        refs=tuple(r.ref for r in rows),
        cited=frozenset(r.source_code for r in rows),
    )
