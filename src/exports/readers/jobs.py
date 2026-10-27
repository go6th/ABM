"""Job Postings mapping (pure). The selection is the job pipeline's own
(``jobs.export.is_exported``, applied by the reader) and descriptions pass the job pipeline's
contact-detail scrub (``jobs.normalise.strip_pii``). The columns follow the owner's master prompt
v2 section 9.4: the class, status and read-type labels come from ``export_rules.yaml labels``;
the entity is the account when the posting link sits on its domains, else the link's host marked
to confirm. Counts are the job pipeline's (``jobs.counts``) plus the row counts and the
platform matrix (total, closed, open, by class) this file recomputes as workbook formulas (B12).
The reference file's columns: the requisition the posting text prints after a job ID label, the
stored location, the sentence the reading quoted for the class (read postings only) and the full
description apart; Last seen and Seniority are not stored by the job pipeline, so not printed."""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlsplit

from exports.config import PlatformEntry, registrable
from exports.content import PostingRow, SnapshotContent
from exports.models import CountLine, JobRow, JobsModel, PlatformClassCounts
from exports.readers.common import MapContext, Mapped, digest, months_before, ref
from jobs.normalise import strip_pii

# the five classes the job pipeline stores; the two derived ones are decided here
_STORED_CLASSES = ("build", "admin_owner", "working_tool", "user", "unclassified")

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def long_date(day: date) -> str:
    """``19 October 2026`` (the reference Summary's date form; no locale involved)."""
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def requisition_of(text: str, pattern: str) -> str:
    """The job ID the posting text prints after a job ID label (``workbooks.jobs
    .requisition_pattern``), else blank: never made up from the link or the feed's own key."""
    found = re.search(pattern, text or "")
    return found.group(1) if found else ""


def posting_text(p: PostingRow) -> str:
    """Title, description and platform sentences as one text (what a platform pattern reads)."""
    return "\n".join((p.title or "", p.description, *p.platform_sentences))


def names_lane_platform(p: PostingRow, platforms: tuple[PlatformEntry, ...]) -> bool:
    """True when the posting names a platform of one of the six lanes: a stored platform name of
    a lane entry, or a lane entry's pattern over the posting text."""
    text = posting_text(p)
    for e in platforms:
        if not e.lane:
            continue
        if any(e.names(name) for name in p.platforms) or e.names(text):
            return True
    return False


def entity_of(url: str, content: SnapshotContent, suffix: str) -> str:
    """The account name when the link is on the account's domain, a subdomain of it or its
    verified careers host; otherwise the link's registrable domain and ``suffix``."""
    host = (urlsplit(url).hostname or "").casefold()
    domain = (content.domain or "").casefold()
    careers = (content.careers_host or "").casefold()
    if domain and (host == domain or host.endswith("." + domain)):
        return content.account_name
    if careers and host == careers:
        return content.account_name
    return f"{registrable(host) or host}{suffix}"


def _class_key(p: PostingRow, platforms: tuple[PlatformEntry, ...]) -> str:
    """The label key of a posting: the stored class, else why it has none (not read in full,
    or read and naming no lane platform), else ``unclassified``."""
    cls = p.posting_class or "unclassified"
    if cls != "unclassified":
        return cls
    if not p.full_text_read:
        return "title_only"
    if not names_lane_platform(p, platforms):
        return "no_lane_platform"
    return "unclassified"


def _source_key(p: PostingRow) -> str:
    if p.origin == "live_board" and p.full_text_read:
        return "live_board_read"
    return "feed_read" if p.full_text_read else "feed_title_only"


def map_jobs(ctx: MapContext) -> Mapped[JobsModel]:
    """Posting rows in the reader's order (posted date, then key), refs ``J-0001``.."""
    c = ctx.content
    where = "jobs"
    labels = ctx.rules.labels
    book = ctx.rules.workbooks.jobs
    if ctx.capped(len(c.postings), ctx.rules.limits.row_caps.postings, where):
        return Mapped(None)
    rows: list[JobRow] = []
    for i, p in enumerate(c.postings, start=1):
        code = ctx.code_or_withhold(None, p.url, where, p.posting_key)
        if not ctx.printable(p.title, where, p.posting_key) or code is None or not p.url:
            continue
        if p.status not in labels.posting_status:
            ctx.fail("posting_status_vocabulary", where, p.posting_key)
            continue
        if (p.posting_class or "unclassified") not in _STORED_CLASSES:
            ctx.fail("posting_class_vocabulary", where, p.posting_key)
            continue
        description = ctx.rendered(strip_pii(p.description), where) if p.full_text_read else ""
        # the sentence the reading quoted for the class: shown for postings read in full only
        read_text = (
            ctx.rendered(p.class_sentence, where) if p.full_text_read and p.class_sentence else ""
        )
        requisition = requisition_of(p.description, book.requisition_pattern)
        rows.append(
            JobRow(
                ref=ref("J", i),
                row_id=p.posting_key,
                title=ctx.rendered(p.title, where),
                status=labels.posting_status[p.status],
                posting_class=labels.posting_class[_class_key(p, ctx.cfg.platforms)],
                platforms=ctx.rendered(", ".join(p.platforms), where),
                platform_sentence=ctx.rendered("\n".join(p.platform_sentences), where),
                description=description,
                url=p.url,
                posted_at=p.posted_at,
                first_seen=p.first_seen,
                posted_or_seen=p.posted_at or p.first_seen,
                entity=ctx.rendered(entity_of(p.url, c, labels.entity_to_confirm_suffix), where),
                source_type=labels.posting_source[_source_key(p)],
                full_text_read=p.full_text_read,
                source_code=code,
                requisition=ctx.rendered(requisition, where) if requisition else "",
                location=ctx.rendered(p.location, where) if p.location else "",
                read_text=read_text,
            )
        )
    withheld = ctx.withheld_in(where)
    if len(rows) + withheld != len(c.postings):
        return Mapped(None)
    open_word, closed_word = labels.posting_status["open"], labels.posting_status["closed"]
    n_open = sum(1 for r in rows if r.status == open_word)
    n_closed = sum(1 for r in rows if r.status == closed_word)
    base = f"postings listed on the {book.sheet} sheet"
    file_lines = (
        CountLine(
            key="file.rows",
            label="Postings in this file",
            value=len(rows),
            base=base,
            formula="rows",
        ),
        CountLine(
            key="file.open",
            label=f"Postings in this file with status {open_word}",
            value=n_open,
            base=base,
            formula="open",
        ),
        CountLine(
            key="file.closed",
            label=f"Postings in this file with status {closed_word}",
            value=n_closed,
            base=base,
            formula="closed",
        ),
        CountLine(
            key="file.withheld",
            label="Postings not listed (no posting link)",
            value=withheld,
            base="postings recorded for this run",
        ),
    )
    pipeline_lines = tuple(
        CountLine(
            key=f"pipeline.{key}",
            label=ctx.rendered(label, where),
            value=value,
            base=ctx.rendered(base_text, where),
        )
        for key, label, value, base_text in (c.jobs_counts.lines if c.jobs_counts else ())
    )
    sentences = tuple(
        ctx.rendered(s, where) for s in (c.jobs_counts.sentences if c.jobs_counts else ())
    )
    for line in file_lines + pipeline_lines:
        ctx.registry.add(f"jobs.{line.key}", line.value, line.base, f"{line.value} {line.label}")
    for i, s in enumerate(sentences):
        ctx.registry.add_sentence(f"jobs.sentence.{i:03d}", s)
    # the platform by class matrix: the workbook recounts it with COUNTIFS over the Jobs sheet
    # (a platform cell that contains the name, any case; the class cell equal to the label)
    class_labels = tuple(labels.posting_class.values())
    matrix: list[PlatformClassCounts] = []
    for name in (*book.matrix_platforms, book.matrix_all_rows):
        if name == book.matrix_all_rows:
            named = rows
        else:
            named = [r for r in rows if name.casefold() in r.platforms.casefold()]
        matrix.append(
            PlatformClassCounts(
                platform=name,
                rows=len(named),
                by_class=tuple(
                    sum(1 for r in named if r.posting_class == label) for label in class_labels
                ),
                open=sum(1 for r in named if r.status == open_word),
                closed=sum(1 for r in named if r.status == closed_word),
            )
        )
    for i, m in enumerate(matrix):
        ctx.registry.add(
            f"jobs.matrix.{i:02d}", m.rows, base, f"{m.rows} postings naming {m.platform}"
        )
        for j, n in enumerate(m.by_class):
            ctx.registry.add(
                f"jobs.matrix.{i:02d}.{j:02d}",
                n,
                f"postings naming {m.platform} on the {book.sheet} sheet",
                f"{n} classed {class_labels[j]}",
            )
    # the Summary sheet's title line and paragraphs (the reference file's Summary)
    texts = ctx.rules.sheet_texts.jobs
    months = ctx.cfg.scope.postings_window_months
    fields = {
        "account": c.account_name,
        "months": months,
        "start": long_date(months_before(c.as_of, months)),
        "end": long_date(c.as_of),
        "as_of": long_date(c.as_of),
        "suffix": labels.entity_to_confirm_suffix.strip(),
    }
    model = JobsModel(
        account=c.account_name,
        as_of=c.as_of,
        rows=tuple(rows),
        counts=file_lines + pipeline_lines,
        sentences=sentences,
        class_labels=class_labels,
        matrix=tuple(matrix),
        summary_title=ctx.rendered(texts.title.format(**fields), where),
        summary_notes=tuple(ctx.rendered(t.format(**fields), where) for t in texts.paragraphs),
    )
    return Mapped(
        model,
        row_ids=tuple(r.row_id for r in rows),
        digests=tuple(digest(r) for r in rows),
        refs=tuple(r.ref for r in rows),
        cited=frozenset(r.source_code for r in rows),
    )
