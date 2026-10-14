"""The deep-dive tables of an account, as spreadsheet sheets: People, Signals, Job postings,
Tech stack and teams, News, From your mail and drive, and Sources.

Each sheet is a fixed definition: a base query over the account's rows, its columns (each with an
allow-listed sort expression), and its filter chips (each a fixed SQL predicate with a live
count). Sorting, filtering and paging run in SQL; nothing from the request is ever put into SQL
except through these allow-lists and bound parameters. Row numbers are absolute (offset + 1).

"From your mail and drive" holds only the signed-in user's own matched items, and the export of
that sheet is labelled private.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.replica.answers import LANE_LABEL, day_month
from account_intelligence.replica.workspace import relative
from exports.policy import leak_hits

GRADE_LABEL = {
    "confirmed": "Confirmed",
    "probable": "Probable",
    "hypothesis": "Hypothesis",
    "lead": "Lead",
    "internal": "Internal",
}
GRADE_ORDER = (
    "CASE {} WHEN 'confirmed' THEN 0 WHEN 'probable' THEN 1 WHEN 'hypothesis' THEN 2 ELSE 3 END"
)


@dataclass(frozen=True)
class Col:
    """One column: its key in the row, its heading, its sort expression and how it is shown."""

    key: str
    label: str
    sort: str | None
    kind: str = "text"  # text, strong, grade, priority, date, ago, link, num


@dataclass(frozen=True)
class Filter:
    """One filter chip: a fixed predicate over the sheet's rows."""

    key: str
    label: str
    where: str


@dataclass(frozen=True)
class Sheet:
    """One deep-dive table."""

    key: str
    title: str
    file: str
    scope: str
    base: str
    cols: tuple[Col, ...]
    filters: tuple[Filter, ...]
    default_sort: str
    default_dir: str = "asc"
    private: bool = False
    footnote: str = "Only Confirmed facts should be said to a prospect."
    tiebreak: str = "s._id"
    # Rows that must never leave in a shareable workbook (internal facts from uploaded files).
    export_where: str = "true"


SORT_HINT = "Click a column heading to sort."
_MONTH = "date_trunc('month', CAST(:now AS timestamptz))"

SHEETS: dict[str, Sheet] = {
    "people": Sheet(
        key="people",
        title="People",
        file="People list.xlsx",
        scope=f"Director and above, global, security excluded. {SORT_HINT}",
        base=(
            "SELECT p.id AS _id, coalesce(p.name, '[Name]') AS name, p.title,"
            " coalesce(p.classification_json->>'area', '') AS area, coalesce(p.priority, '') AS"
            " priority,"
            " coalesce(p.classification_json->>'grade', '') AS grade, p.created_at AS seen,"
            " p.profile_url AS link,"
            " (p.run_id IS DISTINCT FROM (SELECT r.id FROM account_runs r WHERE r.account_id ="
            " p.account_id"
            "   AND r.status IN ('COMPLETED', 'PARTIAL') ORDER BY r.finished_at LIMIT 1)) AS"
            " later_run"
            " FROM people p WHERE p.account_id = :a AND NOT p.excluded AND p.merged_into IS NULL"
            " AND p.erased_at IS NULL AND NOT coalesce(p.internal_only, false)"
        ),
        cols=(
            Col("name", "Name", "s.name", "strong"),
            Col("title", "Role", "s.title"),
            Col("area", "Area", "s.area"),
            Col("priority", "Priority", "s.priority", "priority"),
            Col("grade", "Fact grade", GRADE_ORDER.format("s.grade"), "grade"),
            Col("seen", "Last seen", "s.seen", "ago"),
        ),
        filters=(
            Filter("all", "All", "true"),
            Filter("priority_a", "Priority A", "s.priority = 'A'"),
            Filter("technology", "Technology", "s.area = 'Technology'"),
            Filter("commercial", "Commercial", "s.area = 'Commercial'"),
            Filter("changed", "Changed this month", f"s.later_run AND s.seen >= {_MONTH}"),
        ),
        default_sort="priority",
        tiebreak=GRADE_ORDER.format("s.grade") + ", s.title, s._id",
    ),
    "signals": Sheet(
        key="signals",
        title="Signals",
        file="Signals.xlsx",
        scope=f"Hiring, leadership, technology, news and speaking signals. {SORT_HINT}",
        base=(
            "SELECT s.id AS _id, s.title, initcap(s.signal_type) AS type, s.grade, s.detected_at"
            " AS detected,"
            " (SELECT e.url FROM fact_evidence e WHERE e.fact_id = s.fact_id LIMIT 1) AS link,"
            " (s.detected_at > coalesce((SELECT r.finished_at FROM account_runs r WHERE"
            " r.account_id = s.account_id"
            "   AND r.status IN ('COMPLETED', 'PARTIAL') ORDER BY r.finished_at DESC OFFSET 1"
            " LIMIT 1),"
            "   'infinity'::timestamptz)) AS is_new,"
            " coalesce((SELECT f.visibility = 'private' FROM facts f WHERE f.id = s.fact_id),"
            " false) AS _private"
            " FROM signal_events s WHERE s.account_id = :a"
        ),
        cols=(
            Col("title", "Signal", "s.title", "strong"),
            Col("type", "Type", "s.type"),
            Col("grade", "Fact grade", GRADE_ORDER.format("s.grade"), "grade"),
            Col("detected", "Detected", "s.detected", "date"),
            Col("link", "Source", None, "link"),
        ),
        filters=(
            Filter("all", "All", "true"),
            Filter("new", "New since last refresh", "s.is_new"),
            Filter("hiring", "Hiring", "s.type = 'Hiring'"),
            Filter("leadership", "Leadership", "s.type = 'Leadership'"),
            Filter("news", "News", "s.type = 'News'"),
        ),
        default_sort="detected",
        default_dir="desc",
        export_where="NOT s._private",
    ),
    "jobs": Sheet(
        key="jobs",
        title="Job postings",
        file="Job postings.xlsx",
        scope=f"Open roles from the account's careers pages and job feeds. {SORT_HINT}",
        base=(
            "SELECT j.id AS _id, j.title, array_to_string(j.lanes, ', ') AS lanes,"
            " coalesce(j.location, '') AS location,"
            " coalesce(j.posted_at, j.first_seen) AS posted, initcap(j.status) AS status, j.url AS"
            " link"
            " FROM job_postings j WHERE j.account_id = :a"
        ),
        cols=(
            Col("title", "Role", "s.title", "strong"),
            Col("lanes", "Lane", "s.lanes"),
            Col("location", "Location", "s.location"),
            Col("posted", "Posted", "s.posted", "date"),
            Col("status", "Status", "s.status"),
            Col("link", "Posting", None, "link"),
        ),
        filters=(
            Filter("all", "All", "true"),
            Filter("month", "Posted this month", f"s.posted >= {_MONTH}"),
            Filter("salesforce", "Salesforce", "s.lanes ILIKE '%salesforce%'"),
            Filter("data", "Data and AI", "s.lanes ILIKE '%data%'"),
            Filter("open", "Open", "s.status = 'Open'"),
        ),
        default_sort="posted",
        default_dir="desc",
    ),
    "technology": Sheet(
        key="technology",
        title="Tech stack and teams",
        file="Tech stack and teams.xlsx",
        scope=f"Technologies detected, with how many sources show each. {SORT_HINT}",
        base=(
            "SELECT t.id AS _id, t.technology, t.source_count AS sources,"
            " CASE t.cross_check_result WHEN 'confirmed' THEN 'Confirmed' WHEN 'contradicted' THEN"
            " 'Contradicted'"
            "   WHEN 'not_found' THEN 'Not cross-checked' ELSE '' END AS check,"
            " coalesce(t.first_seen_at, '') AS first_seen, coalesce(t.last_seen_at, '') AS"
            " last_seen,"
            " (SELECT s.url FROM sources s WHERE s.account_id = t.account_id AND s.source_code ="
            " t.source_ref) AS link"
            " FROM tech_detections t WHERE t.account_id = :a"
        ),
        cols=(
            Col("technology", "Technology", "s.technology", "strong"),
            Col("sources", "Sources", "s.sources", "num"),
            Col("check", "Cross check", "s.check"),
            Col("first_seen", "First seen", "s.first_seen"),
            Col("last_seen", "Last seen", "s.last_seen"),
            Col("link", "Source", None, "link"),
        ),
        filters=(
            Filter("all", "All", "true"),
            Filter("confirmed", "Cross-checked", "s.check = 'Confirmed'"),
            Filter("multi", "Two or more sources", "s.sources >= 2"),
        ),
        default_sort="technology",
    ),
    "news": Sheet(
        key="news",
        title="News",
        file="News.xlsx",
        scope=f"Announcements and coverage about the account. {SORT_HINT}",
        base=(
            "SELECT f.id AS _id, coalesce(f.value_text, f.value_json->>'text') AS headline,"
            " f.product_grade AS grade,"
            " e.evidence_date AS published, e.url AS link"
            " FROM facts f LEFT JOIN LATERAL (SELECT url, evidence_date FROM fact_evidence x WHERE"
            " x.fact_id = f.id"
            "   ORDER BY evidence_date DESC NULLS LAST LIMIT 1) e ON true"
            " WHERE f.account_id = :a AND f.attribute_key LIKE 'news.%' AND f.status = 'current'"
            " AND f.visibility = 'public'"
        ),
        cols=(
            Col("headline", "Headline", "s.headline", "strong"),
            Col("grade", "Fact grade", GRADE_ORDER.format("s.grade"), "grade"),
            Col("published", "Published", "s.published", "date"),
            Col("link", "Source", None, "link"),
        ),
        filters=(
            Filter("all", "All", "true"),
            Filter("month", "This month", f"s.published >= {_MONTH}"),
            Filter("confirmed", "Confirmed", "s.grade = 'confirmed'"),
        ),
        default_sort="published",
        default_dir="desc",
    ),
    "mail-and-drive": Sheet(
        key="mail-and-drive",
        title="From your mail and drive",
        file="From your mail and drive.xlsx",
        scope="Your own threads and files that mention this account. Shown only to you.",
        base=(
            "SELECT i.id AS _id, coalesce(i.title, '') AS title, CASE i.item_kind WHEN 'file' THEN"
            " 'File'"
            "   ELSE 'Thread' END AS kind, coalesce(i.location, '') AS location, i.item_date AS"
            " dated,"
            " coalesce(i.snippet, '') AS snippet"
            " FROM connector_items i WHERE i.account_id = :a AND i.owner_user_id = :u AND i.state"
            " = 'matched'"
        ),
        cols=(
            Col("title", "Title", "s.title", "strong"),
            Col("kind", "Kind", "s.kind"),
            Col("location", "From", "s.location"),
            Col("dated", "Date", "s.dated", "date"),
            Col("snippet", "Summary", None),
        ),
        filters=(
            Filter("all", "All", "true"),
            Filter("threads", "Mail threads", "s.kind = 'Thread'"),
            Filter("files", "Files", "s.kind = 'File'"),
        ),
        default_sort="dated",
        default_dir="desc",
        private=True,
        footnote=(
            "From your mail and drive. Private to you, and never included in the research files"
            " you share."
        ),
    ),
    "sources": Sheet(
        key="sources",
        title="Sources",
        file="Sources.xlsx",
        scope=f"Every page the research read for this account. {SORT_HINT}",
        base=(
            "SELECT s0.id AS _id, s0.source_code AS code, coalesce(s0.title, '') AS title,"
            " coalesce(s0.publisher, '') AS publisher, s0.published_at AS published, s0.url AS"
            " link,"
            " EXISTS (SELECT 1 FROM fact_evidence e WHERE e.source_id = s0.id) AS cited"
            " FROM sources s0 WHERE s0.account_id = :a"
        ),
        cols=(
            Col("code", "Code", "s.code"),
            Col("title", "Title", "s.title", "strong"),
            Col("publisher", "Publisher", "s.publisher"),
            Col("published", "Published", "s.published", "date"),
            Col("link", "Link", None, "link"),
        ),
        filters=(
            Filter("all", "All", "true"),
            Filter("cited", "Cited in findings", "s.cited"),
            Filter("month", "Published this month", f"s.published >= {_MONTH}"),
        ),
        default_sort="code",
    ),
}

MAX_PAGE = 500
MAX_EXPORT = 5000


class SheetNotFound(LookupError):
    """No such sheet."""


class BadSheetQuery(ValueError):
    """An unknown sort column, filter or direction."""


@dataclass(frozen=True)
class Query:
    """A validated sheet request."""

    sheet: Sheet
    filter: Filter
    sort: Col
    desc: bool
    offset: int
    limit: int


def parse(
    sheet_key: str,
    filter_key: str | None,
    sort_key: str | None,
    direction: str | None,
    offset: int = 0,
    limit: int = 200,
) -> Query:
    """Validate a request against the sheet's allow-lists."""
    sheet = SHEETS.get(sheet_key)
    if sheet is None:
        raise SheetNotFound(sheet_key)
    filt = next((f for f in sheet.filters if f.key == (filter_key or "all")), None)
    col = next(
        (c for c in sheet.cols if c.key == (sort_key or sheet.default_sort) and c.sort), None
    )
    if filt is None or col is None or (direction or sheet.default_dir) not in ("asc", "desc"):
        raise BadSheetQuery("unknown filter, sort or direction")
    return Query(
        sheet,
        filt,
        col,
        (direction or sheet.default_dir) == "desc",
        max(0, offset),
        max(1, min(limit, MAX_PAGE)),
    )


def display(col: Col, value: Any, now: datetime) -> str:
    """The cell as the sheet shows it (and as the export writes it)."""
    if value is None:
        return ""
    if col.kind == "grade":
        return GRADE_LABEL.get(str(value), str(value).capitalize())
    if col.kind == "ago" and isinstance(value, datetime):
        return relative(value, now)
    if col.kind == "date" and isinstance(value, datetime | date):
        return f"{day_month(value)} {value.year}"
    if isinstance(value, list):
        return ", ".join(str(LANE_LABEL.get(v, v)) for v in value)
    return str(value)


async def read(
    session: Any, q: Query, account_id: UUID, user_id: str, now: datetime, *, export: bool = False
) -> dict[str, Any]:
    """One page of the sheet (or the whole filtered sheet for an export) and the chip counts."""
    params = {"a": account_id, "u": user_id, "now": now}
    counts_sql = ", ".join(
        f"count(*) FILTER (WHERE {f.where}) AS c{i}" for i, f in enumerate(q.sheet.filters)
    )
    counts_query = f"SELECT {counts_sql} FROM ({q.sheet.base}) s"  # nosec B608 -- allow-listed sheet SQL; values are bound.
    counts = (await session.execute(text(counts_query), params)).one()
    order = f"{q.sort.sort} {'DESC' if q.desc else 'ASC'} NULLS LAST, {q.sheet.tiebreak}"
    limit = MAX_EXPORT if export else q.limit
    offset = 0 if export else q.offset
    where = f"({q.filter.where}) AND ({q.sheet.export_where})" if export else q.filter.where
    page_query = f"SELECT * FROM ({q.sheet.base}) s WHERE {where} ORDER BY {order}"  # nosec B608 -- allow-listed sheet SQL; values are bound.
    rows = (
        (
            await session.execute(
                text(page_query + " OFFSET :o LIMIT :l"),
                {**params, "o": offset, "l": limit},
            )
        )
        .mappings()
        .all()
    )
    total = int(counts[[f.key for f in q.sheet.filters].index(q.filter.key)])
    return {
        "key": q.sheet.key,
        "title": q.sheet.title,
        "file": q.sheet.file,
        "scope": q.sheet.scope,
        "footnote": q.sheet.footnote,
        "private": q.sheet.private,
        "columns": [
            {"key": c.key, "label": c.label, "sortable": c.sort is not None, "kind": c.kind}
            for c in q.sheet.cols
        ],
        "filters": [
            {"key": f.key, "label": f.label, "count": int(counts[i])}
            for i, f in enumerate(q.sheet.filters)
        ],
        "filter": q.filter.key,
        "sort": q.sort.key,
        "dir": "desc" if q.desc else "asc",
        "total": total,
        "offset": offset,
        "rows": [
            {
                "n": offset + i + 1,
                "cells": {c.key: display(c, r.get(c.key), now) for c in q.sheet.cols},
                "link": r.get("link") if isinstance(r.get("link"), str) else None,
            }
            for i, r in enumerate(rows)
        ],
    }


class ExportBlocked(Exception):
    """The finished workbook still failed a client-file check; nothing is sent."""

    def __init__(self, checks: tuple[str, ...]) -> None:
        super().__init__("export blocked: " + ", ".join(checks))
        self.checks = checks


WITHHELD = "Withheld for review"
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_DASH = "\u2012\u2013\u2014\u2015"
_SPACED_DASH = re.compile(f"(?<=\\S)\\s+(?:[{_DASH}]|-{{1,2}})\\s+(?=\\S)")
_ANY_DASH = re.compile(f"[{_DASH}]")
# The pre-delivery gate's rule for client files (documents.gate): dash characters, or a hyphen
# used as punctuation between words.
_GATE_DASHES = re.compile(f"[{_DASH}]|\\S\\s+-{{1,2}}\\s+\\S")


def safe_cell(value: Any, *, shareable: bool) -> tuple[Any, bool]:
    """One cell made safe for a client file. Returns (value, withheld).

    Dashes used as punctuation become commas and dash characters become hyphens (the format
    rule); in a shareable workbook a cell that names a data tool, a credit count, a provider code
    or an internal reference is withheld; text that a spreadsheet would run as a formula (it
    starts with ``=``, ``+``, ``-``, ``@``, a tab or a return) gets a leading apostrophe."""
    if not isinstance(value, str):
        return value, False
    text_value = _ANY_DASH.sub("-", _SPACED_DASH.sub(", ", value))
    if shareable and leak_hits(text_value):
        return WITHHELD, True
    if text_value.startswith(_FORMULA_START):
        text_value = "'" + text_value
    return text_value, False


def workbook(sheet: dict[str, Any], account_name: str) -> bytes:
    """The filtered sheet as a real Excel workbook: one sheet, a frozen header, links as text.

    It passes the same checks as every other client file before it leaves: no dash used as
    punctuation, no data tool name, credit count or internal reference (those cells are withheld
    and counted on the About sheet), and no cell a spreadsheet would run as a formula; every
    text cell is stored as text. The finished file is scanned once more as a whole
    (``exports.verify.scan_package``) and refused with ``ExportBlocked`` if anything is left.
    The private sheet (the user's own mail and drive) is labelled private on both sheets and
    is never scanned as shareable: it is exported only to the user it belongs to."""
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    from exports.excel.common import text_cell
    from exports.verify import scan_package

    shareable = not sheet["private"]
    withheld = 0

    def put(ws: Any, row: int, values: list[Any]) -> None:
        nonlocal withheld
        for col, raw in enumerate(values, start=1):
            value, held = safe_cell(raw, shareable=shareable)
            withheld += held
            cell = ws.cell(row=row, column=col)
            if isinstance(value, str):
                text_cell(cell, value)
            else:
                cell.value = value

    book = Workbook()
    ws = book.create_sheet("Private to you" if not shareable else "Sheet 1", 0)
    book.remove(book.worksheets[1])
    cols = sheet["columns"]
    put(ws, 1, ["#"] + [c["label"] for c in cols])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for i, row in enumerate(sheet["rows"], start=2):
        put(
            ws,
            i,
            [row["n"]]
            + [row["link"] if c["kind"] == "link" else row["cells"][c["key"]] for c in cols],
        )
    ws.freeze_panes = "B2"
    for i, c in enumerate(cols, start=2):
        width = max(
            [len(c["label"])]
            + [len(str(r["cells"][c["key"]] or r["link"] or "")) for r in sheet["rows"]]
        )
        ws.column_dimensions[get_column_letter(i)].width = min(60, max(10, width + 2))
    ws.column_dimensions["A"].width = 6
    meta = book.create_sheet("About")
    about: list[list[Any]] = [
        ["Account", account_name],
        ["Table", sheet["title"]],
        ["Filter", next(f["label"] for f in sheet["filters"] if f["key"] == sheet["filter"])],
        ["Rows", len(sheet["rows"])],
        ["Exported", datetime.now(UTC).strftime("%d %b %Y %H:%M UTC")],
    ]
    if withheld:
        about.append(["Withheld", f"{withheld} cells held back for review"])
    if not shareable:
        about.append(["Note", "Private to you. Do not share."])
    for r, line in enumerate(about, start=1):
        put(meta, r, line)
    book.properties.creator = "6th agents"
    book.properties.title = "Private to you" if not shareable else sheet["title"]
    out = io.BytesIO()
    book.save(out)
    data = out.getvalue()
    if shareable:
        from documents.gate import client_text

        failed = tuple(f"client_terms:{h.split(': ')[-1]}" for h in scan_package(data, ()))
        if _GATE_DASHES.search(client_text(data)):
            failed += ("client_dashes",)
        if failed:
            raise ExportBlocked(failed)
    return data
