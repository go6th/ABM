"""Job Postings mapping (pure). The selection is the job pipeline's own
(``jobs.export.is_exported``, applied by the reader) and descriptions pass the job pipeline's
contact-detail scrub (``jobs.normalise.strip_pii``); counts are the job pipeline's
(``jobs.counts``) plus the three row counts this file recomputes as workbook formulas (B12)."""

from __future__ import annotations

from exports.models import CountLine, JobRow, JobsModel
from exports.readers.common import MapContext, Mapped, digest, ref
from jobs.normalise import strip_pii


def map_jobs(ctx: MapContext) -> Mapped[JobsModel]:
    """Posting rows in the reader's order (posted date, then key), refs ``J-0001``.."""
    c = ctx.content
    where = "jobs"
    labels = ctx.rules.labels
    if ctx.capped(len(c.postings), ctx.rules.limits.row_caps.postings, where):
        return Mapped(None)
    rows: list[JobRow] = []
    for i, p in enumerate(c.postings, start=1):
        code = ctx.code_or_fail(None, p.url, where, p.posting_key)
        if not ctx.printable(p.title, where, p.posting_key) or code is None or not p.url:
            continue
        if p.status not in labels.posting_status:
            ctx.fail("posting_status_vocabulary", where, p.posting_key)
            continue
        cls = p.posting_class or "unclassified"
        if cls not in labels.posting_class:
            ctx.fail("posting_class_vocabulary", where, p.posting_key)
            continue
        rows.append(
            JobRow(
                ref=ref("J", i),
                row_id=p.posting_key,
                title=ctx.rendered(p.title, where),
                status=labels.posting_status[p.status],
                posting_class=labels.posting_class[cls],
                platforms=ctx.rendered(", ".join(p.platforms), where),
                platform_sentence=ctx.rendered("\n".join(p.platform_sentences), where),
                description=ctx.rendered(strip_pii(p.description), where),
                url=p.url,
                posted_at=p.posted_at,
                first_seen=p.first_seen,
                source_code=code,
            )
        )
    if len(rows) != len(c.postings):
        return Mapped(None)
    n_open = sum(1 for p in c.postings if p.status == "open")
    n_closed = sum(1 for p in c.postings if p.status == "closed")
    file_lines = (
        CountLine(
            key="file.rows",
            label="Postings in this file",
            value=len(rows),
            base="postings listed on the Postings sheet",
            formula="rows",
        ),
        CountLine(
            key="file.open",
            label="Open postings in this file",
            value=n_open,
            base="postings listed on the Postings sheet",
            formula="open",
        ),
        CountLine(
            key="file.closed",
            label="Closed postings in this file",
            value=n_closed,
            base="postings listed on the Postings sheet",
            formula="closed",
        ),
    )
    pipeline_lines = tuple(
        CountLine(
            key=f"pipeline.{key}",
            label=ctx.rendered(label, where),
            value=value,
            base=ctx.rendered(base, where),
        )
        for key, label, value, base in (c.jobs_counts.lines if c.jobs_counts else ())
    )
    sentences = tuple(
        ctx.rendered(s, where) for s in (c.jobs_counts.sentences if c.jobs_counts else ())
    )
    for line in file_lines + pipeline_lines:
        ctx.registry.add(f"jobs.{line.key}", line.value, line.base, f"{line.value} {line.label}")
    for i, s in enumerate(sentences):
        ctx.registry.add_sentence(f"jobs.sentence.{i:03d}", s)
    model = JobsModel(
        account=c.account_name,
        as_of=c.as_of,
        rows=tuple(rows),
        counts=file_lines + pipeline_lines,
        sentences=sentences,
    )
    return Mapped(
        model,
        row_ids=tuple(r.row_id for r in rows),
        digests=tuple(digest(r) for r in rows),
        refs=tuple(r.ref for r in rows),
        cited=frozenset(r.source_code for r in rows),
    )
