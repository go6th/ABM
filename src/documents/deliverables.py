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
from documents.workspace import MemberRole, may_import, member_roles
from obs.logging import log_context, logger
from research_files.checks import _PII
from research_files.parsers.tabular import MAX_URL_CHARS, safe_url

_log = logger(__name__)

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
FIELD_CHARS = {
    "meets_seniority": 40,
    "location": 200,
    "entity": 200,
    "summary": 2000,
    "sentences": 3000,
    "description": 32000,
    "former_si": 1000,
    "technologies": 4000,
    "locations": 2000,
}
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
            "lanes": ("lane families", "lanes", "lane", "platforms named", "platforms"),
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
            # what only the workbook carries (workspace import phase 2): read in full, the
            # platform sentences and the whole posting text
            "summary": ("what the posting says (read)", "what the posting says", "summary"),
            "sentences": ("platform sentences",),
            "description": ("full description", "description"),
        },
    ),
    SheetKind(
        "people",
        ("name", "full name"),
        (("title as reported", "title", "role", "level", "priority", "linkedin link"),),
        {
            "title": ("title as reported", "title", "role", "position", "designation"),
            "level": ("level", "job level"),
            "lane": ("lane",),
            "priority": ("priority",),
            "side": ("side of the company", "side"),
            "link": ("linkedin link", "linkedin url", "linkedin", "profile link"),
            "second_link": ("second profile",),
            "meets_seniority": ("meets seniority rule",),
            "location": ("location",),
            "note": ("note",),
            "found_by": ("found by",),
            "former_si": (
                "former si and consulting employers (firm, years)",
                "former si and consulting employers (firm and years)",
                "former si and consulting employers",
                "former employers",
            ),
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
            "sources": (
                "postings naming it",
                "postings confirming use",
                "postings describing it as used",
                "postings",
                "sources",
            ),
            "open_now": ("open now",),
            "last_seen": ("last seen",),
            "in_stack": ("in confirmed stack",),
            "checked": ("matches checked by reading",),
        },
    ),
    SheetKind(
        "tech",
        ("team",),
        # the second names of each column are the Platform teams sheet of the 2026-10-26 zip;
        # a plainer teams sheet names the platform and the postings of the last months
        (
            (
                "team path",
                "postings since",
                "lane platforms named",
                "postings count",
                "lane technologies named",
                "platform",
                "platforms",
                "postings in the last 12 months",
            ),
        ),
        {
            "function": ("team path", "job functions", "job functions (postings)"),
            "sources": (
                "postings since",
                "postings count",
                "jobs count",
                "postings in the last 12 months",
                "postings",
            ),
            "lanes": (
                "lane platforms named",
                "lane technologies named",
                "lane technology (postings)",
                "lane technology",
                "platform",
                "platforms",
            ),
            "technologies": (
                "most named technologies",
                "team technologies (postings)",
                "team technologies",
            ),
            "locations": ("top locations", "team locations (postings)", "team locations"),
            "first_activity": ("first activity",),
            "last_activity": ("last activity",),
        },
        note="team",
    ),
)


def norm(column: str) -> str:
    """A column name as the import compares it: lower case, letters, digits and single spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", column.lower())).strip()


# a column may carry this many words beyond an alternative's and still be read as it by the
# word match ("Job Title" for "title", "Seniority level" for "level"); longer headers are
# sentences of their own ("What the posting says (read)")
WORD_MATCH_EXTRA = 1


def _find(
    columns: Sequence[str], alternatives: Sequence[str], *, words: bool = False
) -> str | None:
    """The first column that is, or starts with, one of the alternatives (exact first); with
    ``words`` (a field, never the key or the columns that decide the sheet's kind: owner
    2026-11-05, later packages rename a column a little) else the first column holding every
    word of an alternative with at most ``WORD_MATCH_EXTRA`` more words."""
    normalised = [norm(c) for c in columns]
    for alt in alternatives:
        for col, n in zip(columns, normalised, strict=True):
            if n == alt:
                return col
    for alt in alternatives:
        for col, n in zip(columns, normalised, strict=True):
            if n.startswith(alt + " ") or n.startswith(alt + "s"):
                return col
    if not words:
        return None
    for alt in alternatives:
        want = alt.split()
        for col, n in zip(columns, normalised, strict=True):
            have = n.split()
            if set(want) <= set(have) and len(have) <= len(want) + WORD_MATCH_EXTRA:
                return col
    return None


def field_columns(columns: Sequence[str], kind: SheetKind) -> dict[str, str]:
    """The column each field of ``kind`` is read from. The name key and every field matched by
    its own name (exact or prefix) claim their columns first; the word match then fills the
    fields still open from the columns nobody claimed, so one column is never read as two
    things ("Seniority level" cannot be both level and seniority, a key is never a field)."""
    out: dict[str, str] = {}
    key = _find(columns, kind.key)
    claimed: set[str] = {key} if key else set()
    for stored, alternatives in kind.fields.items():
        col = _find(columns, alternatives)
        if col is not None and col not in claimed:
            out[stored] = col
            claimed.add(col)
    free = [c for c in columns if c not in claimed]
    for stored, alternatives in kind.fields.items():
        if stored in out or not free:
            continue
        col = _find(free, alternatives, words=True)
        if col is not None:
            out[stored] = col
            free.remove(col)
    return out


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
        score = len(field_columns(columns, kind))
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
    # the zip members' workspace roles (``documents.workspace``); empty for a loose upload
    roles: dict[str, MemberRole] = field(default_factory=dict)

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
        if not may_import(self.roles, location.get("member")):
            # a raw pull or build intermediate of a workspace package: its rows are stored
            # as a table, never imported as the account's postings, people or technologies
            self.skipped["rows_not_deliverable"] += 1
            return []
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
        columns = field_columns(list(record), kind)
        for stored in kind.fields:
            col = columns.get(stored)
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
        if len(after) <= 2 or (len(name.split()) >= 2 and all(map(_credential, after))):
            return True
        # the owner's lists carry credential strings the rule above does not know ("B.D.S,
        # M.B.A, PMI-PMP®"): a two-word name followed by short tokens is a name (2026-11-05)
        return (
            len(name.split()) >= 2 and all(len(a.strip()) <= 24 for a in after) and len(text) <= 120
        )
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


async def import_document(
    txn: Any,
    tenant_id: UUID,
    doc: Any,
    *,
    now: datetime,
    payloads: Any | None = None,
    blobs: Any | None = None,
    only_parts: frozenset[str] | None = None,
) -> Counter[str]:
    """Read the document's row passages and record every recognised deliverable row as a fact
    (see the module docstring). Returns the counts: rows read per class, facts written per class,
    rows skipped. A document bound to no account imports nothing. ``only_parts``: the sweep's retry
    of the failed parts of a workspace import (``retry_import_parts``)."""
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
    async with txn(tenant_id) as s:
        # every member the zip listed (a short member whose passage was merged with its
        # neighbours carries no ``member`` of its own), then the members the passages name
        members = set(await repo.member_paths(s, UUID(str(doc.id))))
        members.update(
            str(r.member)
            for r in await s.execute(
                text(
                    "SELECT DISTINCT location->>'member' AS member FROM document_passages"
                    " WHERE document_id = :d AND location ? 'member'"
                ),
                {"d": doc.id},
            )
        )
    roles = member_roles(sorted(members))
    if roles:
        # a workspace package: every row into its native table, once (phase 2), never as a
        # private fact a second time
        from documents.workspace_import import import_workspace

        return await import_workspace(
            txn, tenant_id, doc, roles, now=now, payloads=payloads, blobs=blobs,
            only_parts=only_parts,
        )  # fmt: skip
    owner: str | None = None
    async with txn(tenant_id) as s:
        found_owner = (
            await s.execute(
                text("SELECT owner_user_id FROM documents WHERE tenant_id = :t AND id = :d"),
                {"t": tenant_id, "d": doc.id},
            )
        ).scalar_one_or_none()
        owner = str(found_owner) if found_owner else None
    from documents import import_status

    error: str | None = None
    try:
        counts = await _import_loose(txn, tenant_id, doc, account_id, acct, roles, now)
    except Exception as failure:  # noqa: BLE001 - recorded on the run below, then raised again
        error = f"{type(failure).__name__} ({error_code(failure)})"
        raise
    finally:
        # a loose workbook has no parts: one run, ok or failed
        try:
            await import_status.record_loose(
                txn,
                tenant_id,
                document_id=UUID(str(doc.id)),
                account_id=account_id,
                owner=owner,
                error=error,
            )
        except Exception:  # noqa: BLE001 - never mask the import's own result
            _log.warning("documents.import_status_failed")
    return counts


async def _import_loose(
    txn: Any,
    tenant_id: UUID,
    doc: Any,
    account_id: UUID,
    acct: Any,
    roles: dict[str, Any],
    now: datetime,
) -> Counter[str]:
    """The rows of a loose workbook as private facts (``import_document``)."""
    from account_intelligence.grading.ports import RunInfo
    from account_intelligence.grading.repo import record_document_fact
    from account_intelligence.runs import lifecycle
    from documents.chunking import cite

    counts: Counter[str] = Counter()
    info: RunInfo | None = None  # the import run exists only once a row was recognised
    reader = SheetReader(roles=roles)
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
    cause = getattr(orig, "__cause__", None)  # the driver's own error under SQLAlchemy's wrapper
    for name in ("constraint_name", "sqlstate", "pgcode"):
        for holder in (orig, cause, error):
            value = getattr(holder, name, None)
            if value:
                return str(value)
    return type(error).__name__


async def backfill_imports(
    now: datetime, *, limit: int = 10, payloads: Any | None = None, blobs: Any | None = None
) -> dict[str, int]:
    """Import the documents indexed before the import existed (or whose import failed): per
    client, up to ``limit`` indexed documents bound to an account and not yet marked are claimed
    (marked first, so two sweeps never read one document twice) and read as their owner; a
    document whose import fails is given back for the next pass. Returns the counts of
    documents read and facts written. ``blobs`` and ``payloads`` are the document service's own
    stores: without them a workspace zip's raw pulls, captured pages and posting texts are not
    read again (UCB 2026-11-04: the retry of a failed import left them out)."""
    from account_intelligence.db.session import tenant_txn
    from account_intelligence.db.system_scope import untenanted_session
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
                with log_context(
                    tenant_id=tenant, account_id=doc.account_id, document_id=UUID(str(row.id))
                ):
                    counts = await import_document(
                        as_owner, tenant, doc, now=now, payloads=payloads, blobs=blobs
                    )
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


async def retry_import_parts(
    now: datetime, *, limit: int = 20, payloads: Any | None = None, blobs: Any | None = None
) -> dict[str, int]:
    """Try again the parts of workspace imports that failed or died, per client: only the parts
    ``import_status.due_parts`` hands out (attempts left, wait over), never a part that
    succeeded nor an upload whose import succeeded, each run as the document's owner. The step-5
    analysis is never started from here (it belongs to indexing, once per document). Returns the
    documents and parts tried."""
    from account_intelligence.db.session import tenant_txn
    from account_intelligence.db.system_scope import untenanted_session
    from documents import import_status
    from obs.logging import logger

    log = logger(__name__)
    async with untenanted_session() as plain:
        tenants = [
            UUID(str(r.tenant_id))
            for r in await plain.execute(text("SELECT tenant_id FROM operator_tenant_overview()"))
        ]
    documents = parts = 0
    # LOOP(import part retry clients): cap=every client once per pass, `limit` parts each;
    # exit=last client
    for tenant in tenants:
        async with tenant_txn(tenant, system=True) as s:
            due = await import_status.due_parts(s, tenant, now, limit)
        # LOOP(import part retry documents): cap=the parts claimed above; exit=claims exhausted
        for document_id, wanted in due.items():
            async with tenant_txn(tenant, system=True) as s:
                doc = await repo.get(s, document_id)
            if doc is None or doc.account_id is None:
                continue
            # the import acts as the document's owner (documents are owned by their uploader)
            owner = str(getattr(doc, "owner_user_id", None) or doc.uploaded_by)

            def as_owner(tenant_id: UUID, owner: str = owner) -> Any:
                """A tenant transaction acting as the import's owner (binds the loop's owner)."""
                return tenant_txn(tenant_id, user=owner, system=True)

            try:
                with log_context(
                    tenant_id=tenant, account_id=doc.account_id, document_id=document_id
                ):
                    await import_document(
                        as_owner, tenant, doc, now=now, payloads=payloads, blobs=blobs,
                        only_parts=wanted,
                    )  # fmt: skip
            except Exception as error:  # noqa: BLE001 - one document never stops the pass
                log.warning(
                    "documents.import_retry_failed",
                    extra={
                        "tenant_id": str(tenant),
                        "document_id": str(document_id),
                        "error_class": type(error).__name__,
                        "error_code": error_code(error)[:80],
                    },
                )
                continue
            documents += 1
            parts += len(wanted)
    return {"documents": documents, "parts": parts}
