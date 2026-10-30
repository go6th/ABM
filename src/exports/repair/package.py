"""Packaging of the repaired workspace: CSV and XLSX of every table, the README generated from the
files actually written, and the deterministic zip.

Rules (spec section 10): CSV is UTF-8 with a BOM, every field quoted, one header row; a cell a
spreadsheet would run as a formula gets a leading apostrophe. A workbook is never held back for a
dash: ``clean_cell`` repairs the cell, the finished file is checked with the same whole-workbook
check as every client file (``replica.sheets.check_finished``), and a cell that still trips it is
fixed or, last of all, withheld and logged; the workbook always ships. The README lists every path
and the zip is refused (``PackageInvalid``) when the README and the members disagree.

Pure: no database, network or clock; every date printed is the profile's run date."""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from functools import lru_cache
from typing import Any

from exports.repair.core import Exclusion, RepairedTable, Sheet, WorkspaceProfile
from exports.repair.report import csv_document, exclusions_csv
from exports.urlcheck import is_safe_http_url

DELIVERABLES = "Deliverables/"
TABLES = "Tables/"
INTERNAL = "Internal - your files/"
LOGS = "Logs/"
README = "README.txt"
INTERNAL_MARK = "Internal: do not share"
CELL_MAX = 32_000
CUT_NOTE = " ... (text cut at 32,000 characters; the whole text is in the CSV file)"
MAX_NAME = 150
FORMULA_PREFIX = "__formula__:"
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_DASHES = "‐‑‒–—―−-"
# a dash left at the end of a cell ("Senior Analyst -") or a dash run before text ("- Senior")
_TRAILING_DASH = re.compile(rf"(?<=[^\s{_DASHES}])\s*[{_DASHES}]+\s*$")
_LEADING_DASH = re.compile(rf"^\s*[{_DASHES}]+\s+(?=\S)")


@lru_cache(maxsize=1)
def _theme_link_colour() -> str:
    """The workbook link colour of the export theme (configuration, not a literal here)."""
    from exports.config import load_export_config

    return load_export_config().theme.excel.fonts.link.color


_LINK_COLUMNS = frozenset({"source url", "link", "linkedin"})
_INTERNAL_ONLY_NAME = re.compile(r"(internal|operator)[\s_.\-]*only", re.IGNORECASE)
_UNSAFE_STEM = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_SHEET_TITLE_BAD = re.compile(r"[\[\]:*?/\\]")
_CONTENTS = "Contents:"
_ABOUT_WIDTHS = (18, 60)  # the About sheet's two columns, in characters


class PackageInvalid(ValueError):
    """The archive cannot be built: the README and the members disagree, or a name is unsafe.
    ``missing`` holds the paths (or reasons) at fault."""

    def __init__(self, missing: Sequence[str]) -> None:
        super().__init__("package invalid: " + "; ".join(missing))
        self.missing = list(missing)


# ---- the client-file guard (injected) -----------------------------------------------------------
# The rules every client file passes (format rules, tool names, provider hosts, the finished-file
# check) live in ``replica.sheets`` and ``agents.naming``; ``exports`` may not import either (the
# import contract), so the composition root installs them here (``replica.repair_guard``). The
# fallback below applies the format rules only (formula escape, dashes) and checks nothing else.
_DASH_RUN = f"[{_DASHES}]"
_GATE_DASH_RULE = re.compile("[\u2012\u2013\u2014\u2015]|\\S\\s+-{1,2}\\s+\\S")


class GuardBlocked(Exception):
    """The fallback guard's name for a finished workbook that fails the file check."""


@dataclass(frozen=True)
class ClientGuard:
    """The client-file rules this package applies (see the section comment)."""

    safe_cell: Callable[..., tuple[Any, bool]]
    check_finished: Callable[[bytes], None]
    text_trips: Callable[[str], bool]
    gate_dashes: re.Pattern[str]
    withheld: str
    blocked: type[Exception]


def _fallback_safe_cell(value: Any, *, shareable: bool) -> tuple[Any, bool]:
    if not isinstance(value, str):
        return value, False
    text = _ANY_DASH_RULE.sub("-", value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        text = "'" + text
    return text, False


_ANY_DASH_RULE = re.compile(r"[\u2012\u2013\u2014\u2015]")
FALLBACK_GUARD = ClientGuard(
    safe_cell=_fallback_safe_cell,
    check_finished=lambda data: None,
    text_trips=lambda text: bool(_GATE_DASH_RULE.search(text)),
    gate_dashes=_GATE_DASH_RULE,
    withheld="Withheld for review",
    blocked=GuardBlocked,
)
_installed: ClientGuard = FALLBACK_GUARD


def install_guard(new: ClientGuard) -> None:
    """Install the client-file rules of the running application (composition root)."""
    global _installed
    _installed = new


def guard() -> ClientGuard:
    """The installed guard (the fallback until the application installs its own)."""
    return _installed


# ---- cells ------------------------------------------------------------------------------------


def _strip_edge_dashes(text: str) -> str:
    text = _TRAILING_DASH.sub("", text)
    return _LEADING_DASH.sub("", text)


@lru_cache(maxsize=65536)
def _clean_cached(text: str, shareable: bool, rules: ClientGuard) -> str:
    stripped = _strip_edge_dashes(text)
    out, _held = rules.safe_cell(stripped, shareable=shareable)
    return str(out)


def clean_cell(value: Any, *, shareable: bool) -> str:
    """One cell made safe for a client file, so that the whole-workbook check cannot trip on it
    later: a dash left at either edge of the cell is dropped (the real defect: ``Senior Analyst -``
    beside the next cell's text reads as a spaced hyphen once the workbook text is joined), then
    ``replica.sheets.safe_cell`` applies the format rules (dashes as punctuation, tool names and
    provider domains in a shareable file, the leading apostrophe on text that starts like a
    formula). Never returns anything but a string; ``None`` is the empty string."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if not text:
        return ""
    return _clean_cached(text, bool(shareable), _installed)


def _cut(text: str) -> tuple[str, bool]:
    """The text cut to ``CELL_MAX`` characters at a word boundary, with a visible note."""
    if len(text) <= CELL_MAX:
        return text, False
    room = CELL_MAX - len(CUT_NOTE)
    head = text[:room]
    space = head.rfind(" ")
    if space > room // 2:
        head = head[:space]
    head = _strip_edge_dashes(head.rstrip())
    return head + CUT_NOTE, True


# ---- CSV --------------------------------------------------------------------------------------


def csv_bytes(sheet: Sheet) -> bytes:
    """One sheet as CSV: UTF-8 with a BOM, every field quoted, CRLF, one header row; cells go
    through ``clean_cell`` (a cell starting ``= + - @`` tab or return gets a leading apostrophe).
    A private sheet gets no extra rows (its marker is in the README and its file name)."""
    shareable = not sheet.private
    header = [clean_cell(c, shareable=shareable) for c in sheet.columns]
    rows = [[clean_cell(v, shareable=shareable) for v in r] for r in sheet.cell_rows()]
    return csv_document(header, rows)


# ---- formulas ---------------------------------------------------------------------------------

_FUNCTIONS = frozenset(
    {
        "SUM", "COUNT", "COUNTA", "COUNTIF", "COUNTIFS", "SUMIF", "SUMIFS", "IF", "IFS", "IFERROR",
        "AND", "OR", "NOT", "MIN", "MAX", "ROUND", "ROUNDUP", "ROUNDDOWN", "AVERAGE", "ABS", "INT",
        "LEN", "RANK", "SUMPRODUCT", "VLOOKUP", "INDEX", "MATCH",
    }
)  # fmt: skip
_TOKEN = re.compile(
    r"""
    (?P<ref>(?:'[^'\[\]:*?/\\]{1,31}'!|[A-Za-z_][A-Za-z0-9_]*!)?\$?[A-Z]{1,3}\$?\d{1,7}
        (?::\$?[A-Z]{1,3}\$?\d{1,7})?)(?![A-Za-z0-9_(])
    |(?P<num>\d+(?:\.\d+)?%?)
    |(?P<func>[A-Z][A-Z0-9.]*)\(
    |(?P<op><=|>=|<>|[-+*/^&=<>(),;%])
    """,
    re.VERBOSE,
)
_STRING = re.compile(r'"[^"\r\n]*"')


def valid_formula(formula: str) -> str | None:
    """The formula with spaces outside text removed, or ``None`` when it is not a plain formula:
    it must start with ``=``, hold only cell references, numbers, operators, quoted text and
    functions from a short allow-list, have balanced parentheses and pass the dash rule."""
    if not isinstance(formula, str) or not formula.startswith("=") or len(formula) > 1000:
        return None
    pieces: list[str] = []
    pos = 1
    # LOOP(formula scan): cap=the formula's length (1,000 characters); exit=end of formula
    while pos < len(formula):
        ch = formula[pos]
        if ch.isspace():
            pos += 1
            continue
        if ch == '"':
            m = _STRING.match(formula, pos)
            if not m or guard().gate_dashes.search(m.group(0)):
                return None
            pieces.append(m.group(0))
            pos = m.end()
            continue
        m = _TOKEN.match(formula, pos)
        if not m:
            return None
        if m.group("func") is not None and m.group("func") not in _FUNCTIONS:
            return None
        pieces.append(m.group(0))
        pos = m.end()
    body = "".join(pieces)
    depth = 0
    for p in pieces:
        depth += p.count("(") if not p.startswith('"') else 0
        depth -= p.count(")") if not p.startswith('"') else 0
        if depth < 0:
            return None
    if depth != 0 or not body:
        return None
    return "=" + body


# ---- XLSX -------------------------------------------------------------------------------------


@dataclass
class _Cell:
    text: str
    formula: str | None = None
    href: str | None = None


@dataclass
class _Page:
    title: str
    sheet_name: str
    header: list[str]
    rows: list[list[_Cell]]
    source_columns: tuple[str, ...]


def _sheet_title(name: str, used: set[str]) -> str:
    base = _SHEET_TITLE_BAD.sub(" ", name).strip().strip("'") or "Sheet"
    base = " ".join(base.split())[:31].rstrip() or "Sheet"
    title, n = base, 2
    # LOOP(unique sheet title): cap=the sheets of one workbook; exit=a title not in use
    while title.casefold() in used:
        tail = f" ({n})"
        title = base[: 31 - len(tail)].rstrip() + tail
        n += 1
    used.add(title.casefold())
    return title


def _is_link_column(name: str) -> bool:
    low = name.strip().casefold()
    return low in _LINK_COLUMNS or low.endswith(" url")


def _prepare(
    sheets: Sequence[Sheet], table_title: str, shareable: bool
) -> tuple[list[_Page], list[Exclusion]]:
    rules = guard()
    pages: list[_Page] = []
    exclusions: list[Exclusion] = []
    used = {"about"}
    from openpyxl.utils import get_column_letter

    for sheet in sheets:
        title = _sheet_title(sheet.name, used)
        header = [clean_cell(c, shareable=shareable) or "Column" for c in sheet.columns]
        rows: list[list[_Cell]] = []
        for r_no, row in enumerate(sheet.rows, start=2):
            cells: list[_Cell] = []
            for c_no, col in enumerate(sheet.columns, start=1):
                raw = row.get(col, "")
                raw_text = "" if raw is None else str(raw)
                text = clean_cell(raw_text, shareable=shareable)
                where = f"{title}!{get_column_letter(c_no)}{r_no}"
                if text == rules.withheld and raw_text.strip() != rules.withheld:
                    exclusions.append(
                        Exclusion(table_title, where, "cell withheld for review", f"column {col}")
                    )
                text, was_cut = _cut(text)
                if was_cut:
                    exclusions.append(
                        Exclusion(table_title, where, "cell cut at 32,000 characters", col)
                    )
                formula = valid_formula(str(row.get(FORMULA_PREFIX + col, "")))
                href = text if _is_link_column(col) and is_safe_http_url(text) else None
                cells.append(_Cell(text, formula, href))
            rows.append(cells)
        pages.append(_Page(title, sheet.name, header, rows, sheet.columns))
    return pages, exclusions


def _about_rows(
    pages: Sequence[_Page], account_name: str, title: str, run_date: date, private: bool
) -> list[list[str]]:
    counts = "; ".join(f"{p.sheet_name}: {len(p.rows)}" for p in pages)
    rows = [
        ["Account", account_name],
        ["Table", title],
        ["Rows per sheet", counts],
        ["Exported", run_date.isoformat()],
    ]
    if private:
        rows.append(["Note", INTERNAL_MARK])
    return rows


def _fixed_zip(data: bytes, stamp: tuple[int, int, int, int, int, int]) -> bytes:
    """The same zip with a fixed timestamp on every member, so equal input gives equal bytes."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            zi = zipfile.ZipInfo(info.filename, date_time=stamp)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o600 << 16
            dst.writestr(zi, src.read(info.filename))
    return out.getvalue()


def _render(
    pages: Sequence[_Page],
    about: Sequence[Sequence[str]],
    *,
    title: str,
    run_date: date,
    shareable: bool,
) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    bold = Font(bold=True)
    link_font = Font(color=_theme_link_colour(), underline="single")

    def put_text(ws: Any, row: int, col: int, text: str) -> Any:
        cell = ws.cell(row=row, column=col)
        cell.value = text
        cell.data_type = "s"
        return cell

    book = Workbook()
    if book.active is not None:
        book.remove(book.active)
    for page in pages:
        ws = book.create_sheet(page.title)
        widths = [len(h) for h in page.header]
        for c_no, h in enumerate(page.header, start=1):
            put_text(ws, 1, c_no, h).font = bold
        for r_no, cells in enumerate(page.rows, start=2):
            for c_no, cell in enumerate(cells, start=1):
                if cell.formula is not None:
                    ws.cell(row=r_no, column=c_no).value = cell.formula
                else:
                    put_text(ws, r_no, c_no, cell.text)
                    if cell.href:
                        target = ws.cell(row=r_no, column=c_no)
                        target.hyperlink = cell.href
                        target.font = link_font
                longest = max((len(x) for x in cell.text.split("\n")), default=0)
                widths[c_no - 1] = max(widths[c_no - 1], longest)
        for c_no, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(c_no)].width = min(60, max(8, w + 2))
        ws.freeze_panes = "A2"
    meta = book.create_sheet("About")
    for r_no, line in enumerate(about, start=1):
        for c_no, value in enumerate(line, start=1):
            put_text(meta, r_no, c_no, clean_cell(value, shareable=shareable))
    meta.column_dimensions["A"].width = _ABOUT_WIDTHS[0]
    meta.column_dimensions["B"].width = _ABOUT_WIDTHS[1]
    stamp = datetime.combine(run_date, time())
    book.properties.creator = "6th agents"
    book.properties.lastModifiedBy = "6th agents"
    book.properties.title = clean_cell(title, shareable=shareable)
    book.properties.created = stamp
    book.properties.modified = stamp
    out = io.BytesIO()
    book.save(out)
    return _fixed_zip(out.getvalue(), (run_date.year, run_date.month, run_date.day, 0, 0, 0))


def _strings_in_order(pages: Sequence[_Page], about: Sequence[Sequence[str]]) -> list[str]:
    """The cell texts in the order the workbook's shared-string table holds them (first
    appearance, no repeats): the order in which the joined workbook text puts them side by side."""
    seen: dict[str, None] = {}
    for page in pages:
        for h in page.header:
            seen.setdefault(h)
        for cells in page.rows:
            for c in cells:
                if c.formula is None:
                    seen.setdefault(c.text)
    for line in about:
        for v in line:
            seen.setdefault(v)
    return [s for s in seen if s]


def _text_problems(text: str) -> bool:
    """Whether ``text`` alone trips a check of the finished-file check (the installed guard)."""
    from exports.policy import leak_hits

    return bool(guard().text_trips(text) or leak_hits(text))


def _harden(text: str) -> str:
    """A stricter dash repair for a cell that the normal one left tripping the check."""
    out = re.sub(rf"\s+[{_DASHES}]+\s+", ", ", text)
    out = re.sub(rf"[{_DASHES}]+\s*$", "", out).rstrip()
    out = re.sub(rf"^\s*[{_DASHES}]+\s*", "", out)
    return re.sub("[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]", "-", out)


def _all_cells(pages: Sequence[_Page]) -> list[tuple[_Page, int, int, _Cell]]:
    return [
        (p, r_no, c_no, c)
        for p in pages
        for r_no, cells in enumerate(p.rows, start=2)
        for c_no, c in enumerate(cells, start=1)
        if c.formula is None
    ]


def _offenders(
    pages: Sequence[_Page], about: Sequence[Sequence[str]]
) -> list[tuple[_Page, int, int, _Cell]]:
    """Cells that trip the check alone or beside the next cell of the joined text."""
    cells = _all_cells(pages)
    bad: dict[int, tuple[_Page, int, int, _Cell]] = {}
    for item in cells:
        if item[3].text and _text_problems(item[3].text):
            bad[id(item[3])] = item
    by_text: dict[str, list[tuple[_Page, int, int, _Cell]]] = {}
    for item in cells:
        by_text.setdefault(item[3].text, []).append(item)
    order = _strings_in_order(pages, about)
    for a, b in zip(order, order[1:], strict=False):
        if _text_problems(f"{a} {b}") and not (_text_problems(a) or _text_problems(b)):
            for text in (a, b):
                for item in by_text.get(text, []):
                    bad[id(item[3])] = item
    return list(bad.values())


def write_xlsx_checked(
    table_sheets: Sequence[Sheet],
    account_name: str,
    *,
    private: bool,
    title: str,
    run_date: date,
) -> tuple[bytes, list[Exclusion]]:
    """The workbook of one table, checked as a whole file and always shipped. Returns the bytes
    and the cells that were withheld or cut (for ``Logs/exclusions.csv``).

    A shareable workbook is checked with ``replica.sheets.check_finished``. When that raises
    ``ExportBlocked`` the offending cells are found by scanning each cell alone and each pair of
    neighbouring cells joined by a space, repaired and the workbook is written and checked again
    (up to 3 times); a cell that still fails is replaced by the withheld marker and logged. The
    check is skipped for a private table (the user's own mail and drive), as ``replica.sheets``
    does."""
    rules = guard()
    shareable = not (private or any(s.private for s in table_sheets))
    pages, exclusions = _prepare(table_sheets, title, shareable)
    about = _about_rows(pages, account_name, title, run_date, not shareable)

    def build() -> bytes:
        return _render(pages, about, title=title, run_date=run_date, shareable=shareable)

    data = build()
    if not shareable:
        return data, exclusions
    attempts = 0
    # LOOP(re-export after a refused check): cap=4 attempts; exit=file passes or attempts spent
    while True:
        try:
            rules.check_finished(data)
            return data, exclusions
        except rules.blocked:
            bad = _offenders(pages, about)
            if not bad:
                raise
            attempts += 1
            if attempts > 4:
                raise
            from openpyxl.utils import get_column_letter

            for page, r_no, c_no, cell in bad:
                if attempts <= 3:
                    cell.text = _harden(cell.text)
                else:
                    where = f"{page.title}!{get_column_letter(c_no)}{r_no}"
                    exclusions.append(
                        Exclusion(
                            title,
                            where,
                            "cell withheld for review",
                            f"column {page.header[c_no - 1]}",
                        )
                    )
                    cell.text = rules.withheld
                    cell.href = None
            data = build()


def xlsx_bytes(
    table_sheets: Sequence[Sheet],
    account_name: str,
    *,
    private: bool,
    title: str,
    run_date: date,
) -> bytes:
    """The workbook of one table (see ``write_xlsx_checked``), without the cell log."""
    return write_xlsx_checked(
        table_sheets, account_name, private=private, title=title, run_date=run_date
    )[0]


# ---- README -----------------------------------------------------------------------------------


def _section(members: Sequence[str], prefix: str) -> list[str]:
    return sorted(m for m in members if m.startswith(prefix))


def render_readme(profile: WorkspaceProfile, members: Sequence[str], notes: Sequence[str]) -> str:
    """README.txt generated from the member list that is actually written. The last block,
    ``Contents:``, lists every path once per line; ``validate_readme`` reads only that block."""
    lines = [
        f"Everything collected for {profile.account_name}.",
        f"Exported: {profile.run_date.isoformat()}",
        f"Window: {profile.window_from.isoformat()} to {profile.window_to.isoformat()}",
        "",
    ]

    def block(heading: str, prefix: str, text: str) -> None:
        names = _section(members, prefix)
        lines.append(f"{heading} {text}")
        lines.extend(f"    {n}" for n in names)
        if not names:
            lines.append("    (nothing in this folder)")
        lines.append("")

    block(DELIVERABLES, DELIVERABLES, "the current files of the account.")
    block(TABLES, TABLES, "every table whole, as CSV (all fields quoted) and as Excel.")
    block(
        INTERNAL,
        INTERNAL,
        f"your own files and tables. {INTERNAL_MARK}.",
    )
    block(LOGS, LOGS, "what the repair changed, and every row it left out.")
    lines.append("Not done / Partial:")
    clean_notes = [n.strip() for n in notes if n and n.strip()]
    lines.extend(f"    {n}" for n in clean_notes)
    if not clean_notes:
        lines.append("    Nothing is marked Not done or Partial.")
    lines.append("")
    lines.append(_CONTENTS)
    lines.extend(sorted(members, key=lambda m: (m != README, m)))
    return "\r\n".join(lines) + "\r\n"


def readme_paths(readme_text: str) -> list[str]:
    """The paths listed in the ``Contents:`` block of a README."""
    paths: list[str] = []
    inside = False
    for line in readme_text.splitlines():
        if not inside:
            inside = line.strip() == _CONTENTS
            continue
        if line.strip():
            paths.append(line.rstrip("\r\n"))
    return paths


def validate_readme(readme_text: str, member_names: Iterable[str]) -> list[str]:
    """Every path the README lists that the zip lacks (``missing from archive: <path>``) and every
    member the README does not list (``not in README: <path>``); empty when they agree."""
    members = list(member_names)
    listed = readme_paths(readme_text)
    problems = [f"missing from archive: {p}" for p in listed if p not in set(members)]
    problems += [f"not in README: {m}" for m in members if m not in set(listed)]
    return problems


# ---- names ------------------------------------------------------------------------------------


def _check_member(path: str) -> None:
    bad = (
        not path
        or path.startswith("/")
        or "\\" in path
        or re.match(r"^[A-Za-z]:", path) is not None
        or any(part in ("", ".", "..") or ".." in part for part in path.split("/"))
        or re.search(r"[\x00-\x1f\x7f]", path) is not None
        or len(path) > MAX_NAME
    )
    if bad:
        raise PackageInvalid([f"unsafe member name: {path!r}"])


def _stem(text: str) -> str:
    cleaned = _UNSAFE_STEM.sub(" ", text)
    cleaned = " ".join(cleaned.split()).strip(" .")
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    return cleaned or "table"


def _fit(folder: str, name: str, used: set[str]) -> str:
    """A unique member path ``folder + name`` of at most ``MAX_NAME`` characters."""
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    else:
        ext = "." + ext
    room = MAX_NAME - len(folder) - len(ext) - 6
    stem = stem[: max(8, room)].rstrip(" .") or "file"
    path, n = f"{folder}{stem}{ext}", 2
    # LOOP(unique member name): cap=the members of one archive; exit=a path not in use
    while path.casefold() in used:
        path = f"{folder}{stem} ({n}){ext}"
        n += 1
    used.add(path.casefold())
    _check_member(path)
    return path


def _caller_name(name: str) -> str:
    if "/" in name or "\\" in name or name in ("", ".", "..") or ".." in name:
        raise PackageInvalid([f"unsafe file name: {name!r}"])
    return name


def _timestamp(day: date) -> tuple[int, int, int, int, int, int]:
    return (max(1980, day.year), day.month, day.day, 0, 0, 0)


def build_members(
    profile: WorkspaceProfile,
    tables: Mapping[str, RepairedTable],
    *,
    deliverables: Mapping[str, bytes],
    internal_files: Mapping[str, bytes] | None = None,
    report_md: str = "",
    extra_exclusions: Sequence[Exclusion] = (),
    reserved: Iterable[str] = (),
) -> tuple[dict[str, bytes], list[str]]:
    """Every member except ``README.txt``, by path, and the notes the tables carry for the README.
    ``reserved`` are paths already taken by files the caller streams itself (uploaded files), so
    no name here collides with them."""
    files: dict[str, bytes] = {}
    used: set[str] = {README.casefold(), *(r.casefold() for r in reserved)}
    for name, data in deliverables.items():
        _caller_name(name)
        if _INTERNAL_ONLY_NAME.search(name):
            raise PackageInvalid([f"internal-only file in Deliverables/: {name}"])
        files[_fit(DELIVERABLES, name, used)] = data
    for name, data in (internal_files or {}).items():
        files[_fit(INTERNAL, _caller_name(name), used)] = data

    cell_exclusions: list[Exclusion] = []
    table_notes: list[str] = []
    for table in sorted(tables.values(), key=lambda t: (t.internal, t.title, t.key)):
        private = table.internal or any(s.private for s in table.sheets)
        folder = INTERNAL if table.internal else TABLES
        stem = _stem(table.title) + (" (internal)" if private else "")
        for i, sheet in enumerate(table.sheets):
            name = f"{stem}.csv" if i == 0 else f"{stem} - {_stem(sheet.name)}.csv"
            files[_fit(folder, name, used)] = csv_bytes(sheet)
        data, dropped = write_xlsx_checked(
            table.sheets,
            profile.account_name,
            private=private,
            title=table.title,
            run_date=profile.run_date,
        )
        files[_fit(folder, f"{stem}.xlsx", used)] = data
        cell_exclusions.extend(dropped)
        table_notes.extend(f"{table.title}: {n}" for n in table.notes)
    report = report_md or "# Repair report\n\nNo report text was supplied.\n"
    files[_fit(LOGS, "repair_report.md", used)] = clean_text(report).encode("utf-8")
    files[_fit(LOGS, "exclusions.csv", used)] = exclusions_csv(
        [_clean_table(t) for t in tables.values()],
        [_clean_exclusion(x) for x in [*extra_exclusions, *cell_exclusions]],
    )
    return files, table_notes


_LINE_WITHHELD = "(withheld)"


def _free_text_problems(text: str) -> bool:
    """``_text_problems`` for free text (a README, a report): the dash rules are for cells, so a
    spaced hyphen between words is allowed here; names, hosts and leak rules still trip."""
    g = guard()
    return _text_problems(g.gate_dashes.sub(" ", text))


def clean_text(text: str) -> str:
    """Free text that ships as a member: every line passes the installed guard, a line that trips
    it is replaced (these members do not go through the cell writer)."""
    return "\n".join(
        _LINE_WITHHELD if _free_text_problems(line) else line for line in text.split("\n")
    )


def _clean_exclusion(x: Exclusion) -> Exclusion:
    """An exclusion whose key, reason or detail carries a record's raw text is withheld."""
    if any(_free_text_problems(v) for v in (x.key, x.reason, x.detail) if v):
        return Exclusion(
            x.table,
            _LINE_WITHHELD,
            x.reason if not _free_text_problems(x.reason) else _LINE_WITHHELD,
        )
    return x


def _clean_table(table: RepairedTable) -> RepairedTable:
    """A copy whose exclusions passed ``_clean_exclusion``."""
    from dataclasses import replace

    return replace(table, exclusions=[_clean_exclusion(x) for x in table.exclusions])


def finish_readme(
    profile: WorkspaceProfile, member_names: Iterable[str], notes: Sequence[str]
) -> str:
    """The README for the members that were actually written, validated against them
    (``PackageInvalid`` when it lists a path that is missing or misses a member)."""
    names = [README, *sorted(n for n in member_names if n != README)]
    readme = render_readme(profile, names, notes)
    problems = validate_readme(readme, names)
    if problems:
        raise PackageInvalid(problems)
    return readme


def build_zip(
    profile: WorkspaceProfile,
    tables: Mapping[str, RepairedTable],
    *,
    deliverables: Mapping[str, bytes],
    internal_files: Mapping[str, bytes] | None = None,
    report_md: str = "",
    notes: Sequence[str] = (),
    extra_exclusions: Sequence[Exclusion] = (),
) -> bytes:
    """The workspace archive: ``README.txt``, ``Deliverables/``, ``Tables/`` (CSV and XLSX of every
    non-internal table; an extra CSV ``<Title> - <Sheet name>.csv`` for each further sheet),
    ``Internal - your files/`` (internal files and internal tables) and ``Logs/``
    (``repair_report.md``, ``exclusions.csv``). Deterministic: the same input gives the same bytes
    (fixed timestamps from the run date, README first, then sorted). Raises ``PackageInvalid``
    when a name is unsafe, an internal-only file is offered as a deliverable, or the README and
    the members disagree."""
    files, table_notes = build_members(
        profile,
        tables,
        deliverables=deliverables,
        internal_files=internal_files,
        report_md=report_md,
        extra_exclusions=extra_exclusions,
    )
    readme = finish_readme(profile, files, [*notes, *table_notes])
    names = [README, *sorted(files)]
    out = io.BytesIO()
    stamp = _timestamp(profile.run_date)
    with zipfile.ZipFile(out, "w") as z:
        for name in names:
            zi = zipfile.ZipInfo(name, date_time=stamp)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, readme.encode("utf-8") if name == README else files[name])
    return out.getvalue()
