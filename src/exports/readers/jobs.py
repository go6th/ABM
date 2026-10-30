"""Job Postings mapping (pure). The selection is the job pipeline's own
(``jobs.export.is_exported``, applied by the reader) and descriptions pass the job pipeline's
contact-detail scrub (``jobs.normalise.strip_pii``). The columns follow the owner's reference
Job Postings file (``docs/parity/reference_spec.md``): rows sorted by First seen, newest first;
the class, status and read-type labels come from ``export_rules.yaml labels``; Platforms named in
the fixed order of ``workbooks.jobs.matrix_platforms``; Platform sentences as ``Platform: s1 | s2
|| Other: s3``; the Entity per the account's entity rules (``entity_rules``, data on the stored
entity lock) and else the host: the account name when the link sits on its domains, else the
link's host marked to confirm. Counts are the job pipeline's (``jobs.counts``) plus the row
counts and the platform matrix (total, closed, open, by class, the account's own entity only)
this file recomputes as workbook formulas (B12)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

from exports.config import PlatformEntry, registrable
from exports.content import PostingRow, SnapshotContent
from exports.models import CountLine, JobRow, JobsModel, PlatformClassCounts
from exports.partial import partial_notes
from exports.readers.common import MapContext, Mapped, digest, months_before, ref
from jobs.normalise import strip_pii
from jobs.snapshot import requisition_of

# the five classes the job pipeline stores (every exported posting has one: the heuristic
# decides the postings no reading does, ``jobs.heuristic``; ``unclassified`` is a pipeline error)
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


@dataclass(frozen=True)
class EntityRule:
    """One entity rule of an account: a posting whose title and description match any pattern
    (case sensitive) and, when ``before`` is set, was first seen before that date, is printed
    under ``label``. ``counts_as_account``: the label starts with the account prefix, so the
    Summary matrix counts the row with the account's own positions."""

    label: str
    patterns: tuple[re.Pattern[str], ...]
    before: date | None = None
    counts_as_account: bool = False

    def applies(self, text: str, first_seen: date | None) -> bool:
        """True when this rule names the posting."""
        if self.before is not None and (first_seen is None or first_seen >= self.before):
            return False
        return any(p.search(text) for p in self.patterns)


def entity_prefix(content: SnapshotContent) -> str:
    """The prefix the Summary matrix counts: the entity lock's ``entity_prefix`` when it is a
    prefix of the account name (the short name), else the account name itself (the label every
    account row carries)."""
    name = content.account_name.strip()
    chosen = str((content.entity_lock or {}).get("entity_prefix") or "").strip()
    if chosen and name.casefold().startswith(chosen.casefold()):
        return chosen
    return name


def entity_rules(
    content: SnapshotContent, prefix: str
) -> tuple[tuple[EntityRule, ...], tuple[tuple[str, str], ...]]:
    """The account's entity rules from the stored entity lock (``entities``: a list of
    ``{label, patterns, before, counts_as_account}``) and the problems found: ``(label,
    reason)`` for each rule left out. A rule is left out, never guessed at, when its label is
    blank, it has no pattern, a pattern does not compile, ``before`` is not an ISO date, or
    ``counts_as_account`` disagrees with the label (a label that starts with the prefix counts
    in the account's totals; one that does not, does not)."""
    raw = (content.entity_lock or {}).get("entities")
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        return (), ()
    rules: list[EntityRule] = []
    problems: list[tuple[str, str]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, Mapping):
            problems.append((f"#{i}", "not_a_mapping"))
            continue
        label = str(item.get("label") or "").strip()
        name = label or f"#{i}"
        patterns = item.get("patterns")
        if not label:
            problems.append((name, "label_missing"))
            continue
        if not isinstance(patterns, Sequence) or isinstance(patterns, str) or not patterns:
            problems.append((name, "patterns_missing"))
            continue
        try:
            compiled = tuple(re.compile(str(p)) for p in patterns)
        except re.error:
            problems.append((name, "pattern_does_not_compile"))
            continue
        before: date | None = None
        if item.get("before"):
            try:
                before = date.fromisoformat(str(item["before"])[:10])
            except ValueError:
                problems.append((name, "before_not_an_iso_date"))
                continue
        counts = bool(item.get("counts_as_account"))
        if counts != label.casefold().startswith(prefix.casefold()):
            problems.append((name, "counts_as_account_disagrees_with_label"))
            continue
        rules.append(EntityRule(label, compiled, before, counts))
    return tuple(rules), tuple(problems)


def entity_of(
    url: str,
    content: SnapshotContent,
    suffix: str,
    *,
    rules: Iterable[EntityRule] = (),
    text: str = "",
    first_seen: date | None = None,
) -> str:
    """The Entity of a posting. The account's entity rules come first (the first rule that
    matches ``text``, the title and description, and whose ``before`` date the first seen date
    precedes); then the host: the account name when the link is on the account's domain, a
    subdomain of it or its verified careers host; otherwise the link's registrable domain and
    ``suffix``."""
    for rule in rules:
        if rule.applies(text, first_seen):
            return rule.label
    host = (urlsplit(url).hostname or "").casefold()
    domain = (content.domain or "").casefold()
    careers = (content.careers_host or "").casefold()
    if domain and (host == domain or host.endswith("." + domain)):
        return content.account_name
    if careers and host == careers:
        return content.account_name
    return f"{registrable(host) or host}{suffix}"


def ordered_platforms(names: Iterable[str], order: Sequence[str]) -> tuple[str, ...]:
    """Platform names once each: the fixed ``order`` first (the matrix platforms), then the
    others alphabetically."""
    unique = list(dict.fromkeys(n for n in names if n))
    rank = {n.casefold(): i for i, n in enumerate(order)}
    return tuple(sorted(unique, key=lambda n: (rank.get(n.casefold(), len(rank)), n.casefold())))


_BULLET = re.compile(r"(^|\s)[*\u2022\u00b7]\s+")


def _clean_sentence(text: str) -> str:
    """A sentence on one line: bullet marks removed, whitespace collapsed."""
    return " ".join(_BULLET.sub(r"\1", text).split())


def sentence_cell(
    p: PostingRow,
    order: Sequence[str],
    *,
    per_platform: int,
    max_chars: int,
) -> str:
    """The Platform sentences cell: ``Platform: s1 | s2 || Platform2: s3``, platforms in the
    fixed order, at most ``per_platform`` distinct sentences each, bullet marks removed,
    whitespace collapsed (never a newline), cut at ``max_chars``. A posting that carries only the
    flat sentence list puts each sentence under the first of its platforms the sentence names
    (else the first platform)."""
    pairs = list(p.platform_sentence_pairs)
    if not pairs and p.platform_sentences:
        names = ordered_platforms(p.platforms, order)
        for sentence in p.platform_sentences:
            named = next((n for n in names if n.casefold() in sentence.casefold()), None)
            if named is None and names:
                named = names[0]
            if named is not None:
                pairs.append((named, sentence))
    by_platform: dict[str, list[str]] = {}
    for platform, sentence in pairs:
        clean = _clean_sentence(sentence)
        if clean and clean not in by_platform.setdefault(platform, []):
            by_platform[platform].append(clean)
    parts = [
        f"{name}: {' | '.join(by_platform[name][:per_platform])}"
        for name in ordered_platforms(by_platform, order)
    ]
    return " || ".join(parts)[:max_chars].rstrip()


def _first_seen(p: PostingRow, as_of: date) -> date | None:
    """The First seen date: the feed's first seen, else the posting date, else (a posting only
    the live careers board holds) the date the board was captured."""
    return p.first_seen or p.posted_at or (as_of if p.origin == "live_board" else None)


def _class_key(p: PostingRow) -> str:
    """The label key of a posting: its stored class. The job pipeline gives every exported
    posting one of the four classes (a reading confirms Build and Admin and owner roles; the
    heuristic decides the rest); ``unclassified`` is only a posting the pipeline errored on."""
    return p.posting_class or "unclassified"


def _source_key(p: PostingRow) -> str:
    if p.origin == "live_board" and p.full_text_read:
        return "live_board_read"
    return "feed_read" if p.full_text_read else "feed_title_only"


def map_jobs(ctx: MapContext) -> Mapped[JobsModel]:
    """Posting rows sorted by First seen, newest first, then the posting key; refs ``J-0001``.."""
    c = ctx.content
    where = "jobs"
    labels = ctx.rules.labels
    book = ctx.rules.workbooks.jobs
    if ctx.capped(len(c.postings), ctx.rules.limits.row_caps.postings, where):
        return Mapped(None)
    prefix = entity_prefix(c)
    rules, problems = entity_rules(c, prefix)
    for name, why in problems:  # a bad entity rule is listed in the manifest, never guessed at
        ctx.withhold("jobs.entities", name, f"entity_rule_{why}")
    order = book.matrix_platforms
    yes = labels.yes
    ordered = sorted(
        c.postings,
        key=lambda p: (
            (d := _first_seen(p, c.as_of)) is None,
            -(d.toordinal() if d else 0),
            p.posting_key,
        ),
    )
    rows: list[JobRow] = []
    for p in ordered:
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
        # the sentence the reading quoted for the class: shown for the classes only a reading
        # confirms (Build, Admin and owner), for postings read in full
        key = _class_key(p)
        read_text = (
            ctx.rendered(p.class_sentence, where)
            if p.full_text_read and p.class_sentence and key in book.read_text_classes
            else ""
        )
        requisition = requisition_of(p.description, book.requisition_pattern)
        seen = _first_seen(p, c.as_of)
        platforms = ordered_platforms(p.platforms, order)
        archive = p.archive_url if p.archive_url.startswith(("https://", "http://")) else ""
        rows.append(
            JobRow(
                ref=ref("J", len(rows) + 1),  # listed rows only: refs never skip a number
                row_id=p.posting_key,
                title=ctx.titled(p.title, where),
                status=labels.posting_status[p.status],
                posting_class=labels.posting_class[key],
                platforms=ctx.rendered(", ".join(platforms), where),
                platform_sentence=ctx.rendered(
                    sentence_cell(
                        p,
                        order,
                        per_platform=book.sentences_per_platform,
                        max_chars=book.sentence_chars,
                    ),
                    where,
                ),
                description=description,
                url=p.url,
                posted_at=p.posted_at,
                first_seen=p.first_seen,
                posted_or_seen=p.posted_at or p.first_seen,
                entity=ctx.rendered(
                    entity_of(
                        p.url,
                        c,
                        labels.entity_to_confirm_suffix,
                        rules=rules,
                        text=f"{p.title or ''}\n{p.description}",
                        first_seen=seen,
                    ),
                    where,
                ),
                source_type=labels.posting_source[_source_key(p)],
                full_text_read=p.full_text_read,
                source_code=code,
                requisition=ctx.rendered(requisition, where) if requisition else "",
                location=ctx.rendered(p.location, where) if p.location else "",
                read_text=read_text,
                last_seen=p.last_seen,
                first_seen_text=seen.isoformat() if seen else "",
                last_seen_text=p.last_seen.isoformat() if p.last_seen else "",
                seniority=ctx.rendered(p.seniority, where) if p.seniority else "",
                archive_url=archive,
                new_since_previous=p.new_since_previous,
                new_flag=yes if p.new_since_previous else "",
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
            label="Postings not listed (no usable posting link)",
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
    # (a platform cell that contains the name, the Entity cell that starts with the account
    # prefix, the class cell equal to the label), the seven platforms of the reference
    class_labels = tuple(labels.posting_class[k] for k in book.matrix_classes)
    own = [r for r in rows if r.entity.casefold().startswith(prefix.casefold())]
    matrix: list[PlatformClassCounts] = []
    for name in book.matrix_platforms:
        named = [r for r in own if name.casefold() in r.platforms.casefold()]
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
        "prefix": prefix,
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
        summary_notes=(
            *(ctx.rendered(t.format(**fields), where) for t in texts.paragraphs),
            *(ctx.rendered(n, where) for n in partial_notes(c.tracker, "jobs")),
        ),
        prefix=prefix,
        matrix_heading=ctx.rendered(texts.matrix_heading.format(**fields), where),
    )
    return Mapped(
        model,
        row_ids=tuple(r.row_id for r in rows),
        digests=tuple(digest(r) for r in rows),
        refs=tuple(r.ref for r in rows),
        cited=frozenset(r.source_code for r in rows),
    )
