"""Tech Stack and Teams mapping (pure).

Each detection keeps its first and last seen dates and is labelled a Lead (PRD s3.4 step 6: a
date range in a detection feed is a lead, not a finding). The cross-check counts the postings of
the Job Postings file that name the technology (whole word, any case, in the platforms, platform
sentences, title or description) and states the count with its base. No team data is stored by any
pipeline yet, so the Teams sheet carries the visible "None recorded" note.
"""

from __future__ import annotations

import re

from exports.content import PostingRow
from exports.models import CountLine, TechModel, TechRow
from exports.readers.common import MapContext, Mapped, digest, ref


def names_technology(posting: PostingRow, technology: str) -> bool:
    """True when the posting names the technology as a whole word, any case."""
    pattern = re.compile(rf"(?<!\w){re.escape(technology.strip())}(?!\w)", re.IGNORECASE)
    haystack = "\n".join(
        (*posting.platforms, *posting.platform_sentences, posting.title or "", posting.description)
    )
    return bool(technology.strip()) and bool(pattern.search(haystack))


def map_tech(ctx: MapContext) -> Mapped[TechModel]:
    """Detection rows sorted by technology (then id), refs ``D-0001``.."""
    c = ctx.content
    where = "tech"
    if ctx.capped(len(c.detections), ctx.rules.limits.row_caps.detections, where):
        return Mapped(None)
    postings = c.postings
    rows: list[TechRow] = []
    named_total = 0
    for i, d in enumerate(c.detections, start=1):
        code = ctx.code_or_fail(d.source_code, d.source_url, where, d.id)
        if not ctx.printable(d.technology, where, d.id) or code is None:
            continue
        n = sum(1 for p in postings if names_technology(p, d.technology))
        named_total += 1 if n else 0
        if postings:
            check = f"Named in {n} of {len(postings)} postings in the Job Postings file"
        else:
            check = "No postings in the Job Postings file to cross check"
        ctx.registry.add(f"tech.cross_check.{i:05d}", n, "postings in the Job Postings file", check)
        rows.append(
            TechRow(
                ref=ref("D", i),
                row_id=d.id,
                technology=ctx.rendered(d.technology, where),
                first_seen=ctx.rendered(d.first_seen or "Not given", where),
                last_seen=ctx.rendered(d.last_seen or "Not given", where),
                cross_check=check,
                grade=ctx.rules.labels.detection_grade,
                source_code=code,
            )
        )
    if len(rows) != len(c.detections):
        return Mapped(None)
    counts = (
        CountLine(
            key="file.rows",
            label="Detections in this file",
            value=len(rows),
            base="detections listed on the Detections sheet",
            formula="rows",
        ),
        CountLine(
            key="file.named",
            label="Detections named in at least one posting",
            value=named_total,
            base="detections listed on the Detections sheet",
        ),
    )
    for line in counts:
        ctx.registry.add(f"tech.{line.key}", line.value, line.base, f"{line.value} {line.label}")
    model = TechModel(
        account=c.account_name,
        as_of=c.as_of,
        rows=tuple(rows),
        counts=counts,
        teams_note=ctx.rules.none_found.teams,
    )
    return Mapped(
        model,
        row_ids=tuple(r.row_id for r in rows),
        digests=tuple(digest(r) for r in rows),
        refs=tuple(r.ref for r in rows),
        cited=frozenset(r.source_code for r in rows),
    )
