"""The delivered workbooks' own sheets, as tabs of the deep-dive tables (owner 2026-10-27).

The owner's reference workspace (the BD file set) has several sheets per workbook: People List
(Summary, People), Job Postings (Summary, Jobs, Counts, Long text), Tech Stack and Teams
(Platform Ranking, Stack detections, Platform teams). The web tables showed only each file's
main sheet, so the Summary matrix, the Platform Ranking and the teams were not on screen. Here
the delivered workbook itself is read back (the bytes the client downloads), sheet by sheet, in
the file's order, and each sheet becomes a tab:

* a sheet is a list of blocks in row order: text lines (the title, notes and paragraphs) and
  tables (a header row the builder styled ``Export header``, then its rows down to the next
  blank row); a sheet from elsewhere with no styled header takes its first row of two or more
  cells as the header;
* a formula cell shows the value the formula computes (``workbook_formulas``: the builder stores
  no cached value); a cell's link opens its address and shows the file's text ("Open profile");
* the main sheet (``workbooks.<kind>.sheet``) is marked, so the screen keeps its sortable,
  filterable table there and shows the other sheets as they are.

Reading is bounded: the stored file (``MAX_BYTES``), its unpacked sheets (``MAX_XML_BYTES``,
checked before the workbook is opened), its cells (``MAX_CELLS``), the rows of one table
(``MAX_TABLE_ROWS``) and the workbooks read at once (``READS_AT_ONCE``, in a worker thread). A
read book is cached by tenant, object and SHA-256, after the object is checked to be the
tenant's, and two requests for the same uncached book read it once; a tab switch does not read
the workbook again. A tab's response is capped (``SCREEN_ROWS`` per table, ``SCREEN_CELLS`` and
``SCREEN_CHARS`` per tab). The cells passed every client-file check when the file was built; the
API scrubs the response once more like every sheet, and an export of the file's table passes
the export checks (``table_export``).

GUARANTEE(test_the_web_tabs_are_the_delivered_workbooks_sheets)
GUARANTEE(test_the_book_cache_is_the_tenants_and_reads_a_book_once)
GUARANTEE(test_an_oversized_workbook_is_refused_before_it_is_opened)
GUARANTEE(test_the_file_table_sorts_blanks_last_and_pages_past_the_end)
GUARANTEE(test_a_tab_response_is_capped)
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import threading
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.replica.workbook_formulas import Evaluator

MAX_BYTES = 80 * 1024 * 1024
# the unpacked parts of the package (sheets, shared text, styles): openpyxl holds every cell of
# a workbook it opens, so this, not the stored size, bounds the memory a read takes
MAX_XML_BYTES = 64 * 1024 * 1024
MAX_CELLS = 2_000_000
MAX_TABLE_ROWS = 20_000
CACHE_BOOKS = 6
READS_AT_ONCE = 2
HEADER_STYLE = "Export header"
# sheet key -> the deliverable that prints it (deliverables.kind) and its workbook settings
KINDS = {"people": "people", "jobs": "jobs", "technology": "tech"}
_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Cell:
    """One cell as the file shows it, and the address its link opens (None: no link)."""

    text: str
    href: str | None = None


@dataclass(frozen=True)
class Block:
    """A text line (``kind="text"``) or a table (``kind="table"``) of one sheet."""

    kind: str
    text: str = ""
    header: tuple[str, ...] = ()
    rows: tuple[tuple[Cell, ...], ...] = ()
    cut: int = 0  # rows past MAX_TABLE_ROWS left out (the file holds them)


@dataclass(frozen=True)
class Tab:
    """One sheet of the workbook."""

    key: str
    name: str
    blocks: tuple[Block, ...]
    main: bool = False

    @property
    def table(self) -> Block | None:
        """The sheet's largest table (the main sheet's data table)."""
        tables = [b for b in self.blocks if b.kind == "table"]
        return max(tables, key=lambda b: len(b.rows)) if tables else None


@dataclass(frozen=True)
class Book:
    """A workbook read back: its tabs in the file's order."""

    tabs: tuple[Tab, ...]

    def tab(self, key: str | None) -> Tab | None:
        """The tab with this key; the main tab when ``key`` is None."""
        if key is None:
            return next((t for t in self.tabs if t.main), None)
        return next((t for t in self.tabs if t.key == key), None)


@dataclass(frozen=True)
class Delivered:
    """The delivered file a sheet's tabs come from."""

    id: str
    filename: str
    sha256: str
    object_key: str
    # the data rows the builder counted into the file's manifest (None: not a builder's file)
    rows: int | None = None
    size: int | None = None  # the stored bytes (deliverables.size_bytes)


class TooLarge(ValueError):
    """A workbook past the read bounds (``MAX_XML_BYTES``, ``MAX_CELLS``)."""


def tab_key(name: str) -> str:
    """``Platform Ranking`` -> ``platform-ranking`` (unique within a workbook: see ``read``)."""
    return _SLUG.sub("-", name.lower()).strip("-")[:40].strip("-") or "sheet"


def _text(cell: Any, value: Any) -> str:
    """A value as the file shows it: dates as ISO dates, a percent format as a percentage,
    whole numbers without a decimal point, booleans as Yes or No."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int | float):
        fmt = str(getattr(cell, "number_format", "") or "")
        if "%" in fmt:
            decimals = len(fmt.split(".", 1)[1].split("%", 1)[0]) if "." in fmt else 0
            return f"{value * 100:.{decimals}f}%"
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _href(cell: Any) -> str | None:
    from views.render_safe import safe_link

    link = getattr(cell, "hyperlink", None)
    target = getattr(link, "target", None) if link is not None else None
    return safe_link(target) if isinstance(target, str) else None


def read(data: bytes, main_sheet: str | None) -> Book:
    """Every sheet of the workbook as a tab (see the module docstring); ``TooLarge`` past the
    read bounds."""
    from openpyxl import load_workbook

    _check_package(data)
    wb = load_workbook(io.BytesIO(data), data_only=False)
    grid: dict[tuple[str, int, int], Any] = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.value is not None:
                    grid[(ws.title, c.row, c.column)] = c.value
        if len(grid) > MAX_CELLS:
            raise TooLarge(f"more than {MAX_CELLS} cells")
    ev = Evaluator(grid, [ws.title for ws in wb.worksheets])
    tabs: list[Tab] = []
    seen: set[str] = set()
    for ws in wb.worksheets:
        key = tab_key(ws.title)
        n = 2
        # LOOP(tab key suffix): cap=the workbook's sheets; exit=an unused key
        while key in seen:
            key, n = f"{tab_key(ws.title)}-{n}", n + 1
        seen.add(key)
        tabs.append(Tab(key, ws.title, _blocks(ws, ev), main=ws.title == main_sheet))
    if tabs and not any(t.main for t in tabs):
        tabs[0] = Tab(tabs[0].key, tabs[0].name, tabs[0].blocks, main=True)
    return Book(tuple(tabs))


def _check_package(data: bytes) -> None:
    """``TooLarge`` when the package unpacks past ``MAX_XML_BYTES`` (read from the zip's
    directory, before anything is unpacked)."""
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        unpacked = sum(i.file_size for i in package.infolist())
    if unpacked > MAX_XML_BYTES:
        raise TooLarge(f"unpacks to {unpacked} bytes")


def _blocks(ws: Any, ev: Evaluator) -> tuple[Block, ...]:
    """A sheet's text lines and tables, in row order."""
    rows: dict[int, list[tuple[int, Any]]] = {}
    styled = False
    for row in ws.iter_rows():
        cells = [(c.column, c) for c in row if c.value is not None]
        if cells:
            rows[row[0].row] = cells
            styled = styled or any(_is_header(c) for _, c in cells)
    # without the builder's header style, a header is a row at least 60% as wide as the widest
    widest = max((len(c) for c in rows.values()), default=0)
    unstyled_min = max(2, -(-widest * 3 // 5))
    out: list[Block] = []
    table: dict[str, Any] | None = None
    last_row = 0
    for r in sorted(rows):
        cells = rows[r]
        header = (
            any(_is_header(c) for _, c in cells)
            if styled
            else (table is None and len(cells) >= unstyled_min)
        )
        gap = r > last_row + 1
        last_row = r
        if table is not None and (header or gap):
            out.append(_close(table))
            table = None
        if header:
            width = max(col for col, _ in cells)
            names = {col: _text(c, ev.value(ws.title, r, col)) for col, c in cells}
            table = {"header": tuple(names.get(j, "") for j in range(1, width + 1)), "rows": []}
            continue
        if table is not None:
            width = len(table["header"])
            if len(table["rows"]) >= MAX_TABLE_ROWS:
                table["cut"] = table.get("cut", 0) + 1
                continue
            by_col = dict(cells)
            table["rows"].append(
                tuple(
                    Cell(_text(by_col.get(j), ev.value(ws.title, r, j)), _href(by_col.get(j)))
                    for j in range(1, width + 1)
                )
            )
            continue
        parts = [_text(c, ev.value(ws.title, r, col)) for col, c in cells]
        parts = [p for p in parts if p]
        if parts:
            # a label and its count ("Rank A: 3"; a label that ends in a colon keeps one), else
            # the cells side by side
            two = len(parts) == 2
            line = f"{parts[0].rstrip(':')}: {parts[1]}" if two else " · ".join(parts)
            out.append(Block("text", text=line))
    if table is not None:
        out.append(_close(table))
    return tuple(out)


def _is_header(cell: Any) -> bool:
    try:
        return bool(cell.style == HEADER_STYLE)
    except (AttributeError, IndexError, KeyError):
        return False


def _close(table: dict[str, Any]) -> Block:
    return Block(
        "table", header=table["header"], rows=tuple(table["rows"]), cut=table.get("cut", 0)
    )


# (tenant, object key, SHA-256, main sheet) -> the book read from it. The cache is used from
# the event loop and from the reading threads, so it is guarded by a thread lock (never an
# asyncio primitive, which belongs to one event loop).
CacheKey = tuple[str, str, str, str]
_CACHE: OrderedDict[CacheKey, Book] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_READING: dict[CacheKey, threading.Lock] = {}
_READS = threading.BoundedSemaphore(READS_AT_ONCE)


def _cached(key: CacheKey) -> Book | None:
    with _CACHE_LOCK:
        book = _CACHE.get(key)
        if book is not None:
            _CACHE.move_to_end(key)
        return book


def _keep(key: CacheKey, book: Book) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = book
        _CACHE.move_to_end(key)
        # LOOP(book cache trim): cap=one entry over CACHE_BOOKS; exit=within the cap
        while len(_CACHE) > CACHE_BOOKS:
            _CACHE.popitem(last=False)


def _read_once(key: CacheKey, data: bytes, main_sheet: str, keep: bool) -> Book:
    """``read`` in a worker thread, once per key: a second request for the same book waits for
    the first and takes its result; at most ``READS_AT_ONCE`` workbooks are open at a time."""
    with _CACHE_LOCK:
        gate = _READING.setdefault(key, threading.Lock())
    try:
        with gate:
            book = _cached(key)
            if book is None:
                with _READS:
                    book = read(data, main_sheet)
                if keep:
                    _keep(key, book)
            return book
    finally:
        with _CACHE_LOCK:
            if _READING.get(key) is gate and not gate.locked():
                del _READING[key]


async def book_of(payloads: Any, tenant_id: UUID, found: Delivered, main_sheet: str) -> Book | None:
    """The delivered workbook read back, or None when it cannot be read: not the tenant's,
    missing, too large, or not a workbook. Never raises.

    The object is checked to be the tenant's before anything else, the cache included, and the
    cache is keyed by tenant and object as well as by content, so a book read for one tenant is
    never served to another. A file whose bytes do not match its recorded SHA-256 is shown but
    not cached."""
    from obs.logging import logger
    from tenancy.storage import PathRefused, require_owned

    try:
        require_owned(tenant_id, found.object_key)
    except PathRefused:
        return None
    key: CacheKey = (str(tenant_id), found.object_key, found.sha256, main_sheet)
    hit = _cached(key)
    if hit is not None:
        return hit
    if found.size is not None and found.size > MAX_BYTES:
        return None
    try:
        data = await payloads.get(found.object_key)
        if not isinstance(data, bytes | bytearray) or len(data) > MAX_BYTES:
            return None
        body = bytes(data)
        same = hashlib.sha256(body).hexdigest() == found.sha256
        if not same:
            logger(__name__).warning("file_tabs.sha_mismatch", extra={"file": found.id})
        return await asyncio.to_thread(_read_once, key, body, main_sheet, same)
    except Exception as error:  # noqa: BLE001 - the screen keeps its table without the tabs
        logger(__name__).warning("file_tabs.unreadable", extra={"error": type(error).__name__})
        return None


async def delivered_file(session: Any, account_id: UUID, sheet_key: str) -> Delivered | None:
    """The newest delivered workbook of the account's delivered run that prints ``sheet_key``."""
    from account_intelligence.replica.sheets import delivered_run

    kind = KINDS.get(sheet_key)
    run = await delivered_run(session, account_id) if kind else None
    if run is None:
        return None
    row = (
        await session.execute(
            text(
                "SELECT id, filename, sha256, object_key, size_bytes, manifest_json"
                " FROM deliverables"
                " WHERE account_id = :a AND run_id = :r AND kind = :k"
                " ORDER BY version DESC LIMIT 1"
            ),
            {"a": account_id, "r": run, "k": kind},
        )
    ).first()
    if row is None or not str(row.filename).endswith(".xlsx"):
        return None
    manifest = row.manifest_json
    if isinstance(manifest, str):
        try:
            manifest = json.loads(manifest)
        except ValueError:
            manifest = None  # an unreadable manifest: not a builder's file
    rows = manifest.get("rows") if isinstance(manifest, dict) else None
    return Delivered(
        str(row.id),
        str(row.filename),
        str(row.sha256),
        str(row.object_key),
        rows if isinstance(rows, int) and not isinstance(rows, bool) else None,
        int(row.size_bytes) if row.size_bytes is not None else None,
    )


def builder_main(book: Book, found: Delivered, sheet_key: str) -> Tab | None:
    """The main tab when the file is this builder's own (its manifest counts exactly the main
    table's rows and the table has the configured headers), else None: a file set made some
    other way (the demo seed's stand-in files) is never shown as the files' table."""
    from exports.config import load_export_config

    main = book.tab(None)
    table = main.table if main is not None else None
    if table is None or found.rows is None or len(table.rows) != found.rows:
        return None
    columns = getattr(load_export_config().rules.workbooks, KINDS[sheet_key]).columns
    return main if list(table.header) == [c.header for c in columns] else None


def main_sheet_of(sheet_key: str) -> str:
    """The workbook sheet the deep-dive table shows (``workbooks.<kind>.sheet``)."""
    from exports.config import load_export_config

    books = load_export_config().rules.workbooks
    return str(getattr(books, KINDS[sheet_key]).sheet)


# ---- responses -------------------------------------------------------------------------------

SCREEN_ROWS = 2000  # rows of one table a tab shows (the file holds the rest)
SCREEN_CELLS = 200_000  # cells of every table of one tab
SCREEN_CHARS = 4_000_000  # characters of every cell and line of one tab
SCREEN_LINES = 1000  # text lines of one tab


def tab_refs(book: Book) -> list[dict[str, Any]]:
    """The tab bar: every sheet in the file's order."""
    return [{"key": t.key, "label": t.name, "main": t.main} for t in book.tabs]


def blocks_out(tab: Tab) -> list[dict[str, Any]]:
    """A tab's blocks as the API returns them, capped (``SCREEN_ROWS`` per table, then
    ``SCREEN_CELLS``, ``SCREEN_CHARS`` and ``SCREEN_LINES`` for the whole tab): the rows left out
    are counted in each table's ``cut``, so the screen says the file holds more."""
    out: list[dict[str, Any]] = []
    cells, chars, lines = 0, 0, 0
    for b in tab.blocks:
        if b.kind == "text":
            if lines < SCREEN_LINES and chars + len(b.text) <= SCREEN_CHARS:
                out.append({"kind": "text", "text": b.text})
                lines += 1
                chars += len(b.text)
            continue
        shown: list[list[dict[str, Any]]] = []
        for r in b.rows[:SCREEN_ROWS]:
            size = sum(len(c.text) + len(c.href or "") for c in r)
            if cells + len(r) > SCREEN_CELLS or chars + size > SCREEN_CHARS:
                break
            shown.append([{"text": c.text, "href": c.href} for c in r])
            cells += len(r)
            chars += size
        out.append(
            {
                "kind": "table",
                "header": list(b.header),
                "rows": shown,
                "cut": b.cut + len(b.rows) - len(shown),
            }
        )
    return out


# The main tab read from the file, as a sortable sheet: what the screen shows when the delivered
# run's stored table cannot be used (it was stored under an earlier column set, or not stored):
# the file's columns and cells, in the file's order or sorted by one column, never the live
# research table with other columns.
_NUMERIC = re.compile(r"^-?\d+(?:\.\d+)?%?$")


def _columns(table: Block) -> list[Any]:
    """The table's columns as ``sheets.Col``: a link column (not sortable, like a file-shaped
    sheet's), a number column, a long text column or a text column."""
    from account_intelligence.replica.sheets import Col

    cols = []
    for j, label in enumerate(table.header):
        values = [r[j] for r in table.rows if j < len(r)]
        texts = [c.text for c in values if c.text]
        if any(c.href for c in values):
            kind = "url"
        elif texts and all(_NUMERIC.match(t.strip()) for t in texts):
            kind = "num"
        elif texts and sum(len(t) for t in texts) / len(texts) > 120:
            kind = "long"
        else:
            kind = "text"
        sort = None if kind == "url" else f"c{j + 1}"
        cols.append(Col(f"c{j + 1}", label or f"Column {j + 1}", sort, kind))
    return cols


def grid_sheet(tab: Tab, live: Any) -> Any:
    """The main tab as a ``sheets.Sheet`` (file order, one filter), for the screen and exports."""
    from account_intelligence.replica.sheets import FILE_ORDER, SORT_HINT, Filter, Sheet

    table = tab.table or Block("table")
    return Sheet(
        key=live.key,
        title=live.title,
        file=live.file,
        scope=f"The table of your delivered files, row for row. {SORT_HINT}",
        base="",
        cols=tuple(_columns(table)),
        filters=(Filter("all", "All", "true"),),
        default_sort=FILE_ORDER.key,
        table="file",
        sheet_name=tab.name,
    )


def grid_query(
    tab: Tab, live: Any, sort: str | None, direction: str | None, offset: int, limit: int
) -> Any:
    """A validated request over the main tab (``sheets.BadSheetQuery`` for an unknown or
    unsortable column, or a direction other than asc or desc); the page is clamped like
    ``sheets.parse`` clamps it."""
    from account_intelligence.replica.sheets import FILE_ORDER, MAX_PAGE, BadSheetQuery, Query

    sheet = grid_sheet(tab, live)
    cols = {c.key: c for c in (*sheet.cols, FILE_ORDER)}
    col = cols.get(sort or FILE_ORDER.key)
    if col is None or col.sort is None or (direction or "asc") not in ("asc", "desc"):
        raise BadSheetQuery("unknown sort or direction")
    return Query(
        sheet,
        sheet.filters[0],
        col,
        direction == "desc",
        max(0, offset),
        max(1, min(limit, MAX_PAGE)),
    )


def _sort_value(text_value: str) -> tuple[int, Any]:
    """Numbers (a percentage as its number) before text, text without case."""
    t = text_value.strip()
    if _NUMERIC.match(t):
        return (0, float(t.rstrip("%")))
    return (1, t.casefold())


def _order(tab: Tab, q: Any) -> list[tuple[Cell, ...]]:
    """The main tab's rows in the request's order. A sort is stable (equal cells keep the file's
    order) and puts blank cells last in either direction, as the stored tables do (NULLS LAST)."""
    table = tab.table or Block("table")
    rows = list(table.rows)
    if q.sort.key == "_file":
        return rows[::-1] if q.desc else rows
    j = int(q.sort.key[1:]) - 1

    def cell(r: tuple[Cell, ...]) -> str:
        return r[j].text if j < len(r) else ""

    filled = [r for r in rows if cell(r).strip()]
    blank = [r for r in rows if not cell(r).strip()]
    filled.sort(key=lambda r: _sort_value(cell(r)), reverse=q.desc)
    return filled + blank


def _row(width: int, n: int, r: tuple[Cell, ...]) -> dict[str, Any]:
    cells = {f"c{j + 1}": (r[j].text if j < len(r) else "") for j in range(width)}
    links = {f"c{j + 1}": r[j].href for j in range(min(width, len(r))) if r[j].href}
    return {"n": n, "cells": cells, "links": links, "link": None, "link_label": None}


def grid_rows(tab: Tab, q: Any) -> list[dict[str, Any]]:
    """Every row of the main tab in the request's order, as ``sheets.row_out`` shapes them."""
    width = len((tab.table or Block("table")).header)
    return [_row(width, n, r) for n, r in enumerate(_order(tab, q), start=1)]


def grid_page(tab: Tab, q: Any) -> dict[str, Any]:
    """One page of the main tab in the shape ``sheets.read`` returns (an offset past the end
    is an empty page with the table's total); only the page's rows are shaped."""
    ordered = _order(tab, q)
    width = len((tab.table or Block("table")).header)
    page = [
        _row(width, q.offset + i + 1, r)
        for i, r in enumerate(ordered[q.offset : q.offset + q.limit])
    ]
    return {
        "key": q.sheet.key,
        "title": q.sheet.title,
        "file": q.sheet.file,
        "scope": q.sheet.scope,
        "footnote": q.sheet.footnote,
        "private": False,
        "sheet_name": q.sheet.sheet_name,
        "from_file": True,
        "columns": [
            {"key": c.key, "label": c.label, "sortable": c.sort is not None, "kind": c.kind}
            for c in q.sheet.cols
        ],
        "filters": [{"key": "all", "label": "All", "count": len(ordered)}],
        "filter": "all",
        "sort": q.sort.key,
        "dir": "desc" if q.desc else "asc",
        "total": len(ordered),
        "offset": q.offset,
        "rows": page,
    }


async def iter_rows(tab: Tab, q: Any) -> Any:
    """The main tab's rows for an export (``table_export``), every row in the request's order."""
    width = len((tab.table or Block("table")).header)
    for n, r in enumerate(_order(tab, q), start=1):
        yield _row(width, n, r)
