"""The exact-copy check of an uploaded workspace package (owner 2026-11-05), and the tables
"Download everything" ships for one.

An uploaded workspace is the finished answer, not raw material: what the product stores and
shows for it must be the package, word for word. This module reads the package itself (its
deliverable workbooks sheet by sheet, its Word deliverables table by table) and

* **compares** it with the **table store** (``document_tables`` / ``document_table_rows``):
  every sheet of every deliverable workbook and every table of every Word deliverable, every
  row and every cell as the package has it (typed as the chunker types a cell, a date as
  ``YYYY-MM-DD``, a number as a number: ``documents.chunking.typed_cell``); with the
  **registered files** (``deliverables``): the package's files under the file-import run, byte
  for byte; and with the **native rows** (``people``, ``job_postings``) of that run: one row per
  sheet row, nothing folded (``report``, rendered by ``render``);
* **writes the tables** of the download for an imported account (``table_members``): each
  workbook as it is, each of its sheets as a CSV, each Word deliverable's tables as a workbook
  and as CSVs, every cell as the file has it. No repair stage touches them.

The synthetic test is ``tests/documents/test_workspace_exact_copy_db.py``; the owner's real
packages run through ``tests/documents/test_exact_copy_real_zip.py``.

GUARANTEE(test_an_uploaded_workspace_comes_back_word_for_word)
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import re
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import text

from documents.chunking import normalise_column, typed_cell
from documents.workspace import DELIVERABLE_ROLES, MemberRole, member_roles

MAX_CELL_DIFFS = 40  # per table, in the report
_BLOCK = re.compile(r"^table at block (\d+):")
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


@dataclass
class Grid:
    """One table as the package has it: the header as written and the data rows by row number
    (the header is row 1 of a workbook sheet and of a Word table)."""

    member: str
    name: str  # the sheet name, or the Word table's heading
    header: list[str]
    rows: list[tuple[int, list[Any]]]  # (row number, cells as the parser renders them)
    kind: str = "sheet"  # sheet | docx


@dataclass
class Report:
    """What the check found."""

    account_id: UUID
    document_id: UUID
    sections: list[tuple[str, list[str]]] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)

    def section(self, title: str, lines: Sequence[str]) -> None:
        """Add one titled block of report lines."""
        self.sections.append((title, list(lines)))

    def differ(self, line: str) -> None:
        """Record one difference between the package and what was imported."""
        self.differences.append(line)


# --- the package ------------------------------------------------------------------------------


def _cell(value: Any) -> Any:
    """A workbook cell as ``research_files.parsers.tabular`` renders it."""
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, bytes):
        return value[:64].hex()
    return value


def sheet_grids(member: str, data: bytes) -> list[Grid]:
    """Every sheet of a workbook, as the parser reads it: the header is the first row with a
    value, a row with no value is skipped, trailing empty cells past the header are dropped."""
    from openpyxl import load_workbook

    from research_files.parsers.tabular import linked, sheet_links

    out: list[Grid] = []
    # a cell that shows a link label ("Open profile", "Open posting") is read as the address it
    # opens, as the parser reads it: the label alone is no link
    links = sheet_links(io.BytesIO(data))
    book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        for name in book.sheetnames:
            ws = book[name]
            header: list[str] | None = None
            rows: list[tuple[int, list[Any]]] = []
            for row_no, values in enumerate(ws.iter_rows(values_only=True), start=1):
                if header is None:
                    if not any(v is not None and str(v).strip() for v in values):
                        continue
                    raw = [("" if c is None else str(c)) for c in values]
                    # LOOP(trailing blank headers): cap=the row's cells; exit=a non-blank cell
                    while raw and raw[-1] == "":
                        raw.pop()
                    header = raw
                    continue
                cells = linked([_cell(v) for v in values], row_no, links.get(name, {}))
                # LOOP(trailing empty cells): cap=the row's cells; exit=the header width or a value
                while cells and cells[-1] is None and len(cells) > len(header):
                    cells.pop()
                if all(c is None for c in cells):
                    continue
                rows.append((row_no, cells))
            if header is not None:
                out.append(Grid(member, name, header, rows))
    finally:
        book.close()
    return out


def _docx_cell_text(tc: Any, rels: Mapping[str, str]) -> str:
    """A Word cell's text as the parser renders it: a hyperlink keeps its address as
    ``label (url)`` (``research_files.parsers.documents._docx_text``)."""
    parts: list[str] = []
    inside: set[int] = set()
    for node in tc.iter(f"{_W}hyperlink", f"{_W}t"):
        if node.tag == f"{_W}hyperlink":
            texts = list(node.iter(f"{_W}t"))
            inside.update(id(t) for t in texts)
            label = "".join(t.text or "" for t in texts)
            target = rels.get(str(node.get(f"{_R}id") or ""), "")
            parts.append(f"{label} ({target})" if target and target not in label else label)
        elif id(node) not in inside:
            parts.append(node.text or "")
    return "".join(parts)


def docx_grids(member: str, data: bytes) -> list[Grid]:
    """Every table of a Word file with two rows or more, in document order, named by the heading
    it sits under (a one-row box is prose to the parser too)."""
    from docx import Document

    doc = Document(io.BytesIO(data))
    rels = {
        rid: rel.target_ref
        for rid, rel in doc.part.rels.items()
        if getattr(rel, "is_external", False)
    }
    out: list[Grid] = []
    heading = ""
    n = 0
    for element in doc.element.body.iterchildren():
        if element.tag == f"{_W}p":
            style = element.find(f"{_W}pPr/{_W}pStyle")
            value = str(style.get(f"{_W}val") or "") if style is not None else ""
            if value.lower().startswith(("heading", "title")):
                heading = "".join(t.text or "" for t in element.iter(f"{_W}t")).strip()
            continue
        if element.tag != f"{_W}tbl":
            continue
        rows = [
            [_docx_cell_text(tc, rels) for tc in tr.iter(f"{_W}tc")]
            for tr in element.iter(f"{_W}tr")
        ]
        if len(rows) < 2:
            continue
        n += 1
        out.append(
            Grid(
                member,
                heading or f"table {n}",
                rows[0],
                [(i + 2, r) for i, r in enumerate(rows[1:])],
                kind="docx",
            )
        )
    return out


@dataclass(frozen=True)
class PackageFile:
    """One deliverable member of the package."""

    member: str
    role: MemberRole
    data: bytes

    @property
    def filename(self) -> str:
        """The member's file name without its folder."""
        return self.member.rsplit("/", 1)[-1]

    @property
    def sha256(self) -> str:
        """The SHA-256 of the member's bytes."""
        return hashlib.sha256(self.data).hexdigest()


def package_files(data: bytes) -> list[PackageFile]:
    """The deliverable members of a workspace zip, in zip order."""
    out: list[PackageFile] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        roles = member_roles(z.namelist())
        for info in z.infolist():
            role = roles.get(info.filename)
            if info.is_dir() or role not in DELIVERABLE_ROLES:
                continue
            out.append(PackageFile(info.filename, role, z.read(info)))
    return out


def grids_of(files: Sequence[PackageFile]) -> list[Grid]:
    """Every sheet grid of the given members, in member order."""
    out: list[Grid] = []
    for f in files:
        out.extend(file_grids(f.member, f.data))
    return out


def file_grids(member: str, data: bytes) -> list[Grid]:
    """The tables one deliverable file holds (a workbook's sheets, a Word file's tables)."""
    name = member.casefold()
    if name.endswith(".xlsx"):
        return sheet_grids(member, data)
    if name.endswith(".docx"):
        return docx_grids(member, data)
    return []


# --- the download's tables --------------------------------------------------------------------


def _csv(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(list(header))
    for cells in rows:
        writer.writerow(["" if c is None else str(c) for c in cells])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def _sheet_title(name: str, used: set[str]) -> str:
    """A worksheet title Excel accepts (31 characters, no ``[]:*?/\\``), unique in the book."""
    base = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:31] or "Table"
    title, n = base, 1
    # LOOP(unique sheet title): cap=the titles already used + 1; exit=a title not yet used
    while title.casefold() in used:
        n += 1
        title = f"{base[: 31 - len(str(n)) - 1]} {n}"
    used.add(title.casefold())
    return title


def _docx_workbook(grids: Sequence[Grid]) -> bytes:
    """The tables of one Word deliverable as a workbook, one sheet per table, cells as text."""
    from openpyxl import Workbook

    wb = Workbook()
    first = wb.active
    used: set[str] = set()
    for i, grid in enumerate(grids):
        ws = first if i == 0 and first is not None else wb.create_sheet()
        ws.title = _sheet_title(grid.name, used)
        ws.append(list(grid.header))
        for _row_no, cells in grid.rows:
            ws.append(["" if c is None else str(c) for c in cells])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def table_members(files: Mapping[str, bytes]) -> tuple[dict[str, bytes], list[str]]:
    """The ``Tables/`` members of Download everything for an imported account: each workbook as
    it is (``Tables/<stem>.xlsx``), each of its sheets as ``Tables/<stem> - <sheet>.csv``; each
    Word deliverable's tables as ``Tables/<stem> tables.xlsx`` (a sheet per table) and
    ``Tables/<stem> - <heading>.csv``. ``files`` maps a file name to its bytes. Returns the
    members by path and one line per file for the README."""
    members: dict[str, bytes] = {}
    lines: list[str] = []
    used: set[str] = set()

    def path(name: str) -> str:
        clean = re.sub(r"[^\w .,()&'+-]+", " ", name).strip()[:120]
        candidate, n = f"Tables/{clean}", 1
        # LOOP(unique member path): cap=the paths already used + 1; exit=a path not yet used
        while candidate.casefold() in used:
            n += 1
            stem, dot, ext = clean.rpartition(".")
            candidate = f"Tables/{stem or ext} ({n}){dot + ext if stem else ''}"
        used.add(candidate.casefold())
        return candidate

    for filename, body in files.items():
        stem = filename.rsplit(".", 1)[0]
        grids = file_grids(filename, body)
        if not grids:
            continue
        if filename.casefold().endswith(".xlsx"):
            members[path(filename)] = body
            lines.append(f"{filename}: the workbook as uploaded, {len(grids)} sheet(s) as CSV")
        else:
            members[path(f"{stem} tables.xlsx")] = _docx_workbook(grids)
            lines.append(f"{filename}: {len(grids)} table(s) as a workbook and as CSV")
        for grid in grids:
            members[path(f"{stem} - {grid.name}.csv")] = _csv(
                grid.header, [cells for _row_no, cells in grid.rows]
            )
    return members, lines


def column_lines(files: Mapping[str, bytes]) -> list[str]:
    """What the import read from each workbook sheet, in words, for ``Logs/import_report.md``
    (owner 2026-11-05: a later package renames or adds a column; the change is kept, and said):
    the sheet's kind and, when it has any, the columns read under another name and the columns
    the import has no place for, which stay in the file and its table as written. A sheet the
    import does not understand is kept whole as written."""
    from documents.deliverables import _find, field_columns, match, norm

    lines: list[str] = []
    for filename, body in files.items():
        if not filename.casefold().endswith(".xlsx"):
            continue
        for grid in sheet_grids(filename, body):
            header = [h for h in grid.header if h.strip()]
            if len(header) < 2:
                continue
            label = f"{filename} / {grid.name}"
            kind = match(header)
            if kind is None:
                lines.append(
                    f"{label}: kept as written, {len(grid.rows)} rows"
                    " (not read as people, postings or technologies)"
                )
                continue
            used: dict[str, str] = {}
            key = _find(header, kind.key)
            if key:
                used[key] = "key"
            for stored, col in field_columns(header, kind).items():
                used[col] = stored
            known = {norm(alt) for alts in kind.fields.values() for alt in alts}
            known |= {norm(alt) for alt in kind.key}
            renamed = [
                f"{col} (read as {field.replace('_', ' ')})"
                for col, field in used.items()
                if norm(col) not in known
            ]
            extra = [h for h in header if h not in used]
            line = f"{label}: read as {kind.note or kind.kind}, {len(grid.rows)} rows"
            if renamed:
                line += "; read under another name: " + ", ".join(renamed)
            if extra:
                line += "; kept as written only: " + ", ".join(extra)
            lines.append(line)
    return lines


# --- the store --------------------------------------------------------------------------------


def expected_cells(grid: Grid) -> list[tuple[int, dict[str, Any]]]:
    """The grid's rows as the chunker stores them: typed cells under normalised column names,
    an empty cell left out, a cell past the header named by its position."""
    taken: set[str] = set()
    names = [normalise_column(h, taken, i) for i, h in enumerate(grid.header)]
    out: list[tuple[int, dict[str, Any]]] = []
    for row_no, cells in grid.rows:
        record: dict[str, Any] = {}
        for i, cell in enumerate(cells):
            if i >= len(names):
                names.append(normalise_column("", taken, i))
            value, _kind = typed_cell(None if cell is None else str(cell))
            if value is not None:
                record[names[i]] = value
        out.append((row_no, record))
    return out


def _same(a: Any, b: Any) -> bool:
    if a == b:
        return True
    if isinstance(a, int | float) and isinstance(b, int | float):
        return float(a) == float(b)
    return str(a).strip() == str(b).strip()


async def _stored_tables(s: Any, document_id: UUID) -> list[Any]:
    return list(
        (
            await s.execute(
                text(
                    "SELECT id, member, table_name, columns, row_count FROM document_tables"
                    " WHERE document_id = :d ORDER BY member, created_at, table_name"
                ),
                {"d": document_id},
            )
        ).all()
    )


async def _stored_rows(s: Any, table_id: UUID) -> list[tuple[int, dict[str, Any]]]:
    rows = await s.execute(
        text("SELECT row_no, cells FROM document_table_rows WHERE table_id = :t ORDER BY row_no"),
        {"t": table_id},
    )
    return [(int(r.row_no), dict(r.cells)) for r in rows.all()]


def compare_grid(
    grid: Grid, columns: Sequence[Any], stored: Sequence[tuple[int, dict[str, Any]]]
) -> list[str]:
    """Every difference between a package table and its stored rows, in words."""
    problems: list[str] = []
    raw_names = [str(c.get("raw", "")) for c in columns]
    if raw_names[: len(grid.header)] != grid.header:
        problems.append(f"header differs: file {grid.header} / stored {raw_names}")
    want = expected_cells(grid)
    have = dict(stored)
    missing = [row_no for row_no, _ in want if row_no not in have]
    extra = sorted(set(have) - {row_no for row_no, _ in want})
    if missing:
        problems.append(f"{len(missing)} row(s) of the file are not stored: rows {missing[:20]}")
    if extra:
        problems.append(f"{len(extra)} stored row(s) are not in the file: rows {extra[:20]}")
    shown = 0
    for row_no, record in want:
        got = have.get(row_no)
        if got is None:
            continue
        for name, value in record.items():
            if name not in got or not _same(value, got[name]):
                if shown < MAX_CELL_DIFFS:
                    problems.append(
                        f"row {row_no} {name!r}: file {value!r} / stored {got.get(name)!r}"
                    )
                shown += 1
        for name in got:
            if name not in record and got[name] not in (None, ""):
                if shown < MAX_CELL_DIFFS:
                    problems.append(f"row {row_no} {name!r}: file (empty) / stored {got[name]!r}")
                shown += 1
    if shown > MAX_CELL_DIFFS:
        problems.append(f"... and {shown - MAX_CELL_DIFFS} more cell difference(s)")
    return problems


def _block_no(table_name: str) -> int:
    m = _BLOCK.match(str(table_name))
    return int(m.group(1)) if m else 0


def match_tables(grids: Sequence[Grid], tables: Sequence[Any]) -> dict[int, Any]:
    """The stored table of each grid (by the grid's index): a workbook sheet by member and
    sheet name (the store names it ``<member>:<sheet>``), a Word table by member in document
    order (the store names it by block position)."""
    out: dict[int, Any] = {}
    docx_by_member: dict[str, list[Any]] = {}
    for t in tables:
        if _BLOCK.match(str(t.table_name)):
            docx_by_member.setdefault(str(t.member or ""), []).append(t)
    for member_tables in docx_by_member.values():
        member_tables.sort(key=lambda t: _block_no(t.table_name))
    taken: dict[str, int] = {}
    for i, grid in enumerate(grids):
        if grid.kind == "docx":
            member_tables = docx_by_member.get(grid.member, [])
            at = taken.get(grid.member, 0)
            if at < len(member_tables):
                out[i] = member_tables[at]
                taken[grid.member] = at + 1
            continue
        names = {f"{grid.member}:{grid.name}", grid.name}
        for t in tables:
            if (t.member or "") == grid.member and str(t.table_name) in names:
                out[i] = t
                break
    return out


# --- the report -------------------------------------------------------------------------------


async def report(
    tenant_id: UUID,
    account_id: UUID,
    document_id: UUID,
    package: bytes,
    *,
    payloads: Any | None = None,
) -> Report:
    """Compare what the product stored for ``document_id`` with the package it was uploaded
    from. ``payloads`` (the research payload store) lets the check read the registered files
    back and hash them."""
    from account_intelligence.db.session import tenant_txn

    out = Report(account_id, document_id)
    files = package_files(package)
    grids = grids_of(files)

    async with tenant_txn(tenant_id) as s:
        # 1. the table store, table by table
        tables = await _stored_tables(s, document_id)
        matched = match_tables(grids, tables)
        lines: list[str] = []
        for i, grid in enumerate(grids):
            label = f"{grid.member.rsplit('/', 1)[-1]} / {grid.name}"
            t = matched.get(i)
            if t is None:
                out.differ(f"{label}: not stored as a table")
                lines.append(f"- {label}: {len(grid.rows)} rows → NOT STORED")
                continue
            stored = await _stored_rows(s, UUID(str(t.id)))
            problems = compare_grid(grid, list(t.columns or []), stored)
            mark = "same" if not problems else f"{len(problems)} difference(s)"
            lines.append(
                f"- {label}: file {len(grid.rows)} rows × {len(grid.header)} columns,"
                f" stored {len(stored)} rows → {mark}"
            )
            for p in problems:
                out.differ(f"{label}: {p}")
                lines.append(f"    - {p}")
        out.section("Table store against the package", lines)

        # 2. the registered files
        run_id = await s.scalar(
            text("SELECT id FROM account_runs WHERE account_id = :a AND idempotency_key = :k"),
            {"a": account_id, "k": f"file-import-{document_id}"},
        )
        lines = []
        registered = (
            (
                await s.execute(
                    text(
                        "SELECT kind, filename, sha256, object_key, size_bytes FROM deliverables"
                        " WHERE account_id = :a AND run_id = :r ORDER BY kind"
                    ),
                    {"a": account_id, "r": run_id},
                )
            ).all()
            if run_id
            else []
        )
        by_sha = {str(r.sha256): r for r in registered}
        for f in files:
            if f.role is MemberRole.DELIVERABLE_OTHER:
                continue
            row = by_sha.get(f.sha256)
            if row is None:
                out.differ(f"{f.filename}: not registered as the account's file")
                lines.append(f"- {f.filename}: NOT REGISTERED")
                continue
            note = "registered, same bytes"
            if payloads is not None:
                body = await payloads.get(str(row.object_key))
                if hashlib.sha256(body).hexdigest() != f.sha256:
                    out.differ(f"{f.filename}: the stored file differs from the package's")
                    note = "registered, STORED BYTES DIFFER"
            from documents.workspace_import import safe_filename

            if str(row.filename) != safe_filename(f.filename):
                out.differ(f"{f.filename}: registered under another name ({row.filename})")
            lines.append(f"- {f.filename}: {note} ({row.kind})")
        out.section("Registered files", lines)

        # 3. the native rows against the sheets
        lines = []
        if run_id is None:
            out.differ("no file-import run")
        else:
            people_rows = (
                await s.execute(
                    text(
                        "SELECT name, merged_count FROM people WHERE run_id = :r"
                        " AND merged_into IS NULL AND erased_at IS NULL"
                        " AND source_record_id NOT LIKE 'leadership:%'"
                    ),
                    {"r": run_id},
                )
            ).all()
            jobs_rows = (
                await s.execute(
                    text("SELECT title, merged_count FROM job_postings WHERE run_id = :r"),
                    {"r": run_id},
                )
            ).all()
            people_grid = _grid_named(grids, "name", "title as reported")
            jobs_grids = [g for g in grids if _has(g, "requisition", "title", "status")]
            if people_grid is not None:
                want = _column_values(people_grid, "name")
                have = sorted(str(r.name or "").strip() for r in people_rows)
                lines.append(f"- People sheet rows {len(want)} / people rows {len(have)}")
                _rows_missing(out, lines, "person", want, have)
                folded = sum(int(r.merged_count or 0) for r in people_rows)
                if folded:
                    out.differ(f"{folded} people row(s) folded into another row")
                    lines.append(f"    - {folded} row(s) folded")
            if jobs_grids:
                # every posting sheet (Jobs, and the roles read in full) is in the table
                want = sorted(v for g in jobs_grids for v in _column_values(g, "title"))
                have = sorted(str(r.title or "").strip() for r in jobs_rows)
                lines.append(f"- posting sheet rows {len(want)} / posting rows {len(have)}")
                _rows_missing(out, lines, "posting", want, have)
                folded = sum(int(r.merged_count or 0) for r in jobs_rows)
                if folded:
                    out.differ(f"{folded} posting row(s) folded into another row")
                    lines.append(f"    - {folded} row(s) folded")
        out.section("Native rows against the sheets", lines)
    return out


def _has(grid: Grid, *columns: str) -> bool:
    heads = {h.strip().casefold() for h in grid.header}
    return grid.kind == "sheet" and all(c in heads for c in columns)


def _grid_named(grids: Sequence[Grid], *columns: str) -> Grid | None:
    """The first workbook sheet whose header has every named column (case folded)."""
    return next((g for g in grids if _has(g, *columns)), None)


def _column_values(grid: Grid, column: str) -> list[str]:
    i = next(i for i, h in enumerate(grid.header) if h.strip().casefold() == column)
    return sorted(
        str(cells[i]).strip()
        for _, cells in grid.rows
        if i < len(cells) and cells[i] not in (None, "")
    )


def _rows_missing(
    out: Report, lines: list[str], what: str, want: list[str], have: list[str]
) -> None:
    """Multiset difference of the sheet's values and the stored ones (names and titles repeat;
    a repeat in the sheet is a repeat in the table)."""
    missing = list((Counter(want) - Counter(have)).elements())
    extra = list((Counter(have) - Counter(want)).elements())
    if missing:
        out.differ(f"{len(missing)} {what} row(s) of the sheet are not stored")
        lines.append(f"    - not stored ({len(missing)}): {missing[:25]}")
    if extra:
        lines.append(f"    - stored but not in the sheet as written ({len(extra)}): {extra[:25]}")


def render(rep: Report) -> str:
    """The report as Markdown."""
    head = (
        "SAME: the package came back whole"
        if not rep.differences
        else f"{len(rep.differences)} DIFFERENCE(S)"
    )
    parts = [
        f"# Exact-copy check\n\nAccount {rep.account_id}, document {rep.document_id}\n\n"
        f"**{head}**\n"
    ]
    for title, lines in rep.sections:
        parts.append(f"\n## {title}\n\n" + "\n".join(lines) + "\n")
    if rep.differences:
        parts.append("\n## Differences\n\n" + "\n".join(f"- {d}" for d in rep.differences) + "\n")
    return "".join(parts)
