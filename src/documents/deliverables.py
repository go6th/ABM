"""Structured import of the standing deliverable workbooks a client uploads (2026-10-16).

A client uploads the files the research produces (an earlier manual research of the account, or
a workspace zip): the Job Postings, People List and Tech Stack and Teams workbooks of the owner's
skill (``config/skill/archive/references/output-spec.md``). Until now such a file was indexed
for Ask and read for "fit" facts only, so the account page showed no job postings, people or
technologies from it, and the owner asked why the data was stored but not shown.

This module recognises those sheets by their header row (by name, or by the first row that is
a header when the sheet opens with a title block, as the Tech Stack workbook does) and records
each data row as a graded fact of the right class, keyed as the research graph keys its own
findings (``jobs.<row>.<slug>``, ``people.<row>.<slug>``, ``tech.<row>.<slug>``), private and
Internal, citing the file and the row (``grading.repo.record_document_fact``). The deep-dive
counts, sheets and the Sources list then show them, attributed to the uploaded file. No model is
called and nothing leaves the system; the import runs when indexing finishes, so the data is on
the page as soon as the file is processed.

Rules: a work email, phone or address column is never stored, and an email address or phone
number written inside any other cell (a Note, a Location) is removed from the stored fields and
quote (minimisation); a link field (``link``, ``second_link``, ``url``) keeps only a safe http(s)
address (``tabular.safe_url``: a cell still showing its label, "Open profile", or a ``mailto:``
or ``javascript:`` target is no link) and every other field is bounded (``FIELD_CHARS``); a row
without its key cell (title, name, technology, team) is skipped and counted; the import is
idempotent per document and row (``write_key``); a sheet no kind matches (a Summary sheet, prose)
yields nothing.

GUARANTEE(test_uploaded_deliverable_workbooks_show_as_job_postings_people_and_tech)
GUARANTEE(test_a_title_block_sheet_is_read_from_its_header_row)
GUARANTEE(test_people_fields_never_keep_a_work_email_or_phone)
GUARANTEE(test_a_link_field_keeps_only_a_safe_web_address)
GUARANTEE(test_a_people_list_with_a_location_column_is_read_as_people)
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import text

from documents import repo
from documents.extract import slug
from research_files.checks import _PII
from research_files.parsers.tabular import MAX_URL_CHARS, safe_url

IMPORT_VERSION = "deliverables-import-v1"
PAGE = 500
# rows a table may open with before its header (a title block); after that the table is prose
HEADER_SEARCH_ROWS = 40
# the tracker row each class is recorded under; the prefix is the class the sheets count
ROW_KEY = {
    "jobs": ("jobs", "job_postings_read_classified"),
    "people": ("people", "full_profiles_senior_people"),
    "tech": ("tech", "technology_detections"),
}
NEVER_STORED = re.compile(r"(?i)\b(e-?mail|phone|mobile|address)\b")
# fields that hold a web address: kept only when the cell is one (``safe_url``), up to its length
URL_FIELDS = frozenset({"link", "second_link", "url"})
# the longest value a field keeps; a free-text cell longer than this is cut
FIELD_CHARS = {"meets_seniority": 40, "location": 200, "entity": 200}
DEFAULT_FIELD_CHARS = 500
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_COLUMN_N = re.compile(r"^column (\d+)$")


@dataclass(frozen=True)
class SheetKind:
    """One workbook sheet the import understands: its class, the column that names each fact,
    the alternatives that must all be present (one per group), and the fields kept per row."""

    kind: str
    key: tuple[str, ...]
    required: tuple[tuple[str, ...], ...]
    fields: Mapping[str, tuple[str, ...]]
    note: str = ""


KINDS: tuple[SheetKind, ...] = (
    SheetKind(
        "jobs",
        ("title", "role", "job title"),
        (("status", "first seen", "requisition", "posted", "location"),),
        {
            "lanes": ("lane families", "lanes", "lane", "platforms named"),
            "location": ("location",),
            "posted": ("first seen", "posted", "date posted", "posted at"),
            "last_seen": ("last seen",),
            "status": ("status",),
            "requisition": ("requisition", "requisition id", "req id"),
            "seniority": ("seniority",),
            "entity": ("entity",),
            "side": ("side of the company", "side"),
            "class": ("class",),
            "url": ("url", "link", "posting link", "posting url"),
        },
    ),
    SheetKind(
        "people",
        ("name", "full name"),
        (("title as reported", "title", "role", "level", "priority", "linkedin link"),),
        {
            "title": ("title as reported", "title", "role"),
            "level": ("level",),
            "lane": ("lane",),
            "priority": ("priority",),
            "side": ("side of the company", "side"),
            "link": ("linkedin link", "linkedin url", "linkedin", "profile link"),
            "second_link": ("second profile",),
            "meets_seniority": ("meets seniority rule",),
            "location": ("location",),
            "note": ("note",),
            "found_by": ("found by",),
        },
    ),
    SheetKind(
        "tech",
        ("technology", "platform"),
        (
            (
                "category",
                "business function",
                "rank",
                "postings naming it",
                "postings confirming use",
                "lane",
            ),
        ),
        {
            "category": ("category",),
            "function": ("business function", "function"),
            "lane": ("lane for this account", "lane"),
            "rank": ("rank",),
            "sources": ("postings naming it", "postings confirming use", "postings", "sources"),
            "open_now": ("open now",),
            "last_seen": ("last seen",),
        },
    ),
    SheetKind(
        "tech",
        ("team",),
        # the second names of each column are the Platform teams sheet of the 2026-10-26 zip
        (
            (
                "team path",
                "postings since",
                "lane platforms named",
                "postings count",
                "lane technologies named",
            ),
        ),
        {
            "function": ("team path", "job functions"),
            "sources": ("postings since", "postings count"),
            "lanes": ("lane platforms named", "lane technologies named"),
            "technologies": ("most named technologies", "team technologies"),
            "locations": ("top locations", "team locations"),
        },
        note="team",
    ),
)


def norm(column: str) -> str:
    """A column name as the import compares it: lower case, letters, digits and single spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", column.lower())).strip()


def _find(columns: Sequence[str], alternatives: Sequence[str]) -> str | None:
    """The first column that is, or starts with, one of the alternatives (exact first)."""
    normalised = [norm(c) for c in columns]
    for alt in alternatives:
        for col, n in zip(columns, normalised, strict=True):
            if n == alt:
                return col
    for alt in alternatives:
        for col, n in zip(columns, normalised, strict=True):
            if n.startswith(alt + " ") or n.startswith(alt + "s"):
                return col
    return None


def match(columns: Sequence[str]) -> SheetKind | None:
    """The sheet kind these column names belong to, or None: of the kinds whose key and required
    columns are all present, the one whose fields name the most columns (the first on a tie). A
    People List has a Title and a Location column too, so the first kind that fits is not
    enough: it would read every person as a job posting titled with the person's title."""
    best: SheetKind | None = None
    best_score = -1
    for kind in KINDS:
        if _find(columns, kind.key) is None:
            continue
        if not all(_find(columns, group) is not None for group in kind.required):
            continue
        score = sum(
            _find(columns, alternatives) is not None for alternatives in kind.fields.values()
        )
        if score > best_score:
            best, best_score = kind, score
    return best


def segments(content: str, prefix: str | None = None) -> list[tuple[str, str]]:
    """The ``column: value`` pairs of a row passage. ``prefix`` is the first column's name with
    its ``: `` when it is known to contain ``: `` itself (a title-block sheet whose first header
    cell is the sheet's title); the other pairs split at their first ``: ``."""
    out = []
    for i, part in enumerate(content.split("; ")):
        if i == 0 and prefix and part.startswith(prefix):
            out.append((prefix[:-2].strip(), part[len(prefix) :].strip()))
        elif ": " in part:
            column, value = part.split(": ", 1)
            out.append((column.strip(), value.strip()))
    return out


def title_prefix(first_parts: Sequence[str]) -> str | None:
    """The ``<title>: `` every row of a title-block sheet opens with: the common prefix of two
    rows' first parts, cut after its last ``: ``; None when the rows share none."""
    if len(first_parts) < 2:
        return None
    a, b = first_parts[0], first_parts[1]
    n = 0
    # LOOP(common prefix): cap=the shorter first part; exit=first differing character
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    common = a[:n]
    cut = common.rfind(": ")
    return common[: cut + 2] if cut > 0 else None


@dataclass(frozen=True)
class Found:
    """One structured row: its class, the value that names it, the fields kept, the quote."""

    kind: str
    value: str
    fields: dict[str, str]
    quote: str
    seq: int
    location: dict[str, Any]


@dataclass
class _Table:
    """Where the reader is with one sheet: unresolved (rows pending until the header is found),
    resolved by column names, or by position under a title-block header."""

    rows_seen: int = 0
    kind: SheetKind | None = None
    header: list[str] | None = None  # positional header (a title-block sheet), else None
    prefix: str | None = None  # the first column's ``<title>: `` of a title-block sheet
    pending: list[Any] = field(default_factory=list)
    gave_up: bool = False


@dataclass
class SheetReader:
    """Reads row passages table by table, finding each table's header once."""

    tables: dict[str, _Table] = field(default_factory=dict)
    skipped: Counter[str] = field(default_factory=Counter)
    parts: dict[tuple[str, Any], list[Any]] = field(default_factory=dict)

    def read(self, passage: Any) -> list[Found]:
        """The structured rows one ``crm_row`` passage yields (none, one, or the rows a table
        held back). A row the chunker split into parts (``part`` of ``parts`` in the location) is
        read once its last part arrives, joined back with ``; `` into the whole row."""
        location = dict(passage.location or {})
        if int(location.get("parts") or 1) > 1:
            key = (
                f"{location.get('member') or ''}/{location.get('table') or 'table'}",
                location.get("row"),
            )
            have = self.parts.setdefault(key, [])
            have.append(passage)
            if len(have) < int(location["parts"]):
                return []
            del self.parts[key]
            have.sort(key=lambda p: int(dict(p.location)["part"]))
            whole = {k: v for k, v in location.items() if k not in ("part", "parts")}
            passage = SimpleNamespace(
                content="; ".join(p.content for p in have), location=whole, seq=have[0].seq
            )
        return self._read(passage)

    def _read(self, passage: Any) -> list[Found]:
        """The structured rows one whole ``crm_row`` passage yields (none, one, or the rows a table
        held back while its header was still unknown)."""
        location = dict(passage.location or {})
        name = f"{location.get('member') or ''}/{location.get('table') or 'table'}"
        table = self.tables.setdefault(name, _Table())
        if table.gave_up or not passage.content:
            return []
        if table.kind is not None:
            found = self._row(table, passage)
            return [found] if found is not None else []
        table.rows_seen += 1
        # a sheet with a real header: the column names say what the sheet is
        if (kind := match([c for c, _ in segments(passage.content)])) is not None:
            table.kind = kind
            found = self._row(table, passage)
            return [found] if found is not None else []
        # a title-block sheet: hold the rows until two show the title (the first column's
        # name), then look for the first row whose VALUES are a header
        table.pending.append(passage)
        if table.prefix is None:
            table.prefix = title_prefix([p.content.split("; ")[0] for p in table.pending])
            if table.prefix is None:
                if table.rows_seen >= HEADER_SEARCH_ROWS:
                    table.gave_up, table.pending = True, []
                return []
        out: list[Found] = []
        pending, table.pending = table.pending, []
        for row in pending:
            if table.kind is None:
                values = [v for _, v in segments(row.content, table.prefix)]
                if (kind := match(values)) is not None:
                    table.kind, table.header = kind, values
                continue  # a title row, or the header row itself: not a fact
            found = self._row(table, row)
            if found is not None:
                out.append(found)
        if table.kind is None and table.rows_seen >= HEADER_SEARCH_ROWS:
            table.gave_up = True
        return out

    def _row(self, table: _Table, passage: Any) -> Found | None:
        """One data row of a resolved table as a ``Found``, or None when it names nothing."""
        kind = table.kind
        assert kind is not None
        segs = segments(passage.content, table.prefix)
        record = self._record(table, segs)
        key_col = _find(list(record), kind.key)
        value = (record.get(key_col) or "").strip() if key_col else ""
        if not value or value.lower() in ("n/a", "none", "-", "null"):
            self.skipped[f"{kind.kind}_no_key"] += 1
            return None
        if not plausible_key(kind.kind, value):
            # a cell that is not a name or a technology: a number from a ranking sheet, a JSON
            # blob of schools or skills from a profile export, a long comma list (the QuidelOrtho
            # export of 2026-10-21 listed 5,900 such "people" and "1", "3", "6" as technologies)
            self.skipped[f"{kind.kind}_key_not_plausible"] += 1
            return None
        fields: dict[str, str] = {}
        for stored, alternatives in kind.fields.items():
            col = _find(list(record), alternatives)
            raw = (record.get(col) or "").strip() if col else ""
            if not raw or raw.lower() in ("n/a", "none", "-", "null"):
                continue
            if NEVER_STORED.search(col or ""):
                continue
            if stored in URL_FIELDS:
                url = safe_url(raw)  # "Open profile" (a link label), mailto:, javascript: are not
                if url is not None:
                    fields[stored] = url[:MAX_URL_CHARS]
                continue
            raw = scrub(raw)
            if stored in ("posted", "last_seen"):
                raw = raw[:10] if _DATE.match(raw) else raw[:40]
            if stored == "status":
                raw = raw[:1].upper() + raw[1:].lower()
            fields[stored] = raw[: FIELD_CHARS.get(stored, DEFAULT_FIELD_CHARS)]
        if kind.note:
            fields["category"] = kind.note
        quote = "; ".join(
            f"{c}: {v if safe_url(v) else scrub(v)}"
            for c, v in zip(record, record.values(), strict=True)
            if v and not NEVER_STORED.search(c)
        )
        location = dict(passage.location or {})
        value = scrub(value)
        return Found(kind.kind, value[:500], fields, quote[:600], int(passage.seq), location)

    @staticmethod
    def _record(table: _Table, segs: list[tuple[str, str]]) -> dict[str, str]:
        """The row as ``column -> value`` under the table's real header."""
        if table.header is None:
            return dict(segs)
        out: dict[str, str] = {}
        for i, (col, val) in enumerate(segs):
            m = _COLUMN_N.match(col.strip().lower())
            position = int(m.group(1)) - 1 if m else (0 if i == 0 else i)
            if 0 <= position < len(table.header):
                out[table.header[position]] = val
        return out


_NUMERIC = re.compile(r"^[\d.,%\s-]+$")


def scrub(text: str) -> str:
    """The text with every email address and phone number removed: a Note or Location cell can
    hold a person's work contact, which the import never stores (see the module docstring)."""
    if "@" in text:
        text = _PII["email"].sub("[email removed]", text)
    return _PII["phone"].sub("[phone removed]", text)


def _credential(part: str) -> bool:
    """Whether a comma-separated part after a name is a credential: short, at most two words,
    mostly capitals (PhD, MSc, PMP, SHRM-SCP, PRINCE 2)."""
    text = part.strip()
    letters = [c for c in text if c.isalpha()]
    return (
        0 < len(text) <= 15
        and len(text.split()) <= 2
        and bool(letters)
        and 2 * sum(c.isupper() for c in letters) >= len(letters)
    )


def plausible_key(kind: str, value: str) -> bool:
    """Whether a key cell can be what its column says: a person's name or a technology is
    short prose, never a number, a JSON or list literal, or a long comma-separated list."""
    text = value.strip()
    if not text or _NUMERIC.match(text):
        return False
    if text[0] in "[{" or text.endswith(("]", "}")):
        return False
    if kind == "people":
        if len(text) > 80 or not any(c.isalpha() for c in text):
            return False
        # a name may carry its credentials ("Synthetic Person, PhD, PMP, CSM": ten such rows in
        # the reference People List of 2026-10-27); a list of skills or schools does not look
        # like that
        name, *after = text.split(",")
        return len(after) <= 2 or (len(name.split()) >= 2 and all(map(_credential, after)))
    if kind == "tech":
        return len(text) <= 120 and text.count(",") <= 3 and any(c.isalpha() for c in text)
    return True


def attribute_key(found: Found) -> str:
    """``<class>.<tracker row>.<slug>``; a posting's slug carries its requisition so two postings
    with one title stay two facts."""
    prefix, row = ROW_KEY[found.kind]
    extra = found.fields.get("requisition") or found.fields.get("function") or ""
    return f"{prefix}.{row}.{slug(f'{extra} {found.value}'.strip())}"


def config_hash() -> str:
    """What the import's rules were when a fact was written (recorded on the fact)."""
    return hashlib.sha256(IMPORT_VERSION.encode("utf-8")).hexdigest()


async def import_document(txn: Any, tenant_id: UUID, doc: Any, *, now: datetime) -> Counter[str]:
    """Read the document's row passages and record every recognised deliverable row as a fact
    (see the module docstring). Returns the counts: rows read per class, facts written per class,
    rows skipped. A document bound to no account imports nothing."""
    from account_intelligence.grading.ports import RunInfo
    from account_intelligence.grading.repo import record_document_fact
    from account_intelligence.runs import lifecycle
    from documents.chunking import cite

    counts: Counter[str] = Counter()
    if doc.account_id is None:
        return counts
    async with txn(tenant_id) as s:
        acct = (
            await s.execute(
                text("SELECT domain, name FROM accounts WHERE id = :a"), {"a": doc.account_id}
            )
        ).first()
        if acct is None:
            return counts
    account_id = UUID(str(doc.account_id))
    info: RunInfo | None = None  # the import run exists only once a row was recognised
    reader = SheetReader()
    cfg_hash = config_hash()
    after = -1
    # LOOP(import pages): cap=passages / PAGE pages; exit=a short page
    while True:
        async with txn(tenant_id) as s:
            rows = await repo.passages_after(s, doc.id, after, PAGE)
        if not rows:
            break
        after = int(rows[-1].seq)
        found = [f for r in rows if r.kind == "crm_row" for f in reader.read(r)]
        async with txn(tenant_id) as s:
            if found and info is None:
                run_id = await lifecycle.record_file_import_run(
                    s, tenant_id, account_id, UUID(str(doc.id)), now=now
                )
                info = RunInfo(tenant_id, account_id, run_id, now, str(acct.domain), acct.name)
            for item in found:
                assert info is not None
                location = {**item.location, "passage": item.seq, "cite": cite(item.location)}
                _, created = await record_document_fact(
                    s,
                    run=info,
                    attribute_key=attribute_key(item),
                    value=item.value,
                    quote=item.quote,
                    document_id=UUID(str(doc.id)),
                    location=location,
                    authoritative=bool(doc.authoritative),
                    flags=("workspace_import", f"category_{item.kind}"),
                    config_hash=cfg_hash,
                    fields=item.fields,
                )
                counts[f"{item.kind}_rows"] += 1
                counts[f"{item.kind}_written"] += int(created)
        if len(rows) < PAGE:
            break
    counts.update(reader.skipped)
    return counts


def error_code(error: BaseException) -> str:
    """The database constraint or SQL state behind a failure, else the class name (for the log)."""
    orig = getattr(error, "orig", None)
    for name in ("constraint_name", "sqlstate", "pgcode"):
        value = getattr(orig, name, None) or getattr(error, name, None)
        if value:
            return str(value)
    return type(error).__name__


async def backfill_imports(now: datetime, *, limit: int = 10) -> dict[str, int]:
    """Import the documents indexed before the import existed (or whose import failed): per
    client, up to ``limit`` indexed documents bound to an account and not yet marked are claimed
    (marked first, so two sweeps never read one document twice) and read as their owner; a
    document whose import fails is given back for the next pass. Returns the counts of
    documents read and facts written."""
    from account_intelligence.db.session import tenant_txn, untenanted_session
    from obs.logging import logger

    log = logger(__name__)
    async with untenanted_session() as plain:
        tenants = [
            UUID(str(r.tenant_id))
            for r in await plain.execute(text("SELECT tenant_id FROM operator_tenant_overview()"))
        ]
    documents = facts = 0
    # LOOP(import backfill clients): cap=every client once per pass, `limit` documents each;
    # exit=last client
    for tenant in tenants:
        async with tenant_txn(tenant, system=True) as s:
            rows = await repo.claim_imports(s, now, limit)
        # LOOP(import backfill documents): cap=`limit` per client per pass; exit=rows exhausted
        for row in rows:
            owner = str(row.owner_user_id)

            def as_owner(tenant_id: UUID, owner: str = owner) -> Any:
                """A tenant transaction acting as the import's owner (binds the loop's owner)."""
                return tenant_txn(tenant_id, user=owner, system=True)

            try:
                async with as_owner(tenant) as s:
                    doc = await repo.get(s, UUID(str(row.id)))
                if doc is None:
                    continue
                counts = await import_document(as_owner, tenant, doc, now=now)
            except Exception as error:  # noqa: BLE001 - one document never stops the pass
                log.warning(
                    "documents.import_failed",
                    extra={
                        "tenant_id": str(tenant),
                        "error_class": type(error).__name__,
                        "error_code": error_code(error)[:80],
                    },
                )
                async with tenant_txn(tenant, system=True) as s:
                    await repo.unmark_imported(s, UUID(str(row.id)))
                continue
            documents += 1
            facts += sum(v for k, v in counts.items() if k.endswith("_written"))
    return {"documents": documents, "facts": facts}
