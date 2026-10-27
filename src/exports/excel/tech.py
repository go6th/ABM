"""Tech Stack and Teams workbook, in the shape of the owner's reference file set
(BD_Tech_Stack_and_Teams.xlsx): three sheets in this order.

* Platform Ranking: the title line, the base line with its number in the share column (the cell
  every share formula divides by), the rule paragraphs, then the ranked table (rank and share
  as formulas, master prompt v2 section 7.3), the counts per rank letter (COUNTIF formulas) and
  the platforms seen once (platform, category and lane only).
* The stack sheet (``workbooks.tech.sheet``): the title line, the note, then one row per
  technology detection. It stands where the reference has its confirmed stack and is named for
  what it holds: a detection is a lead, not a finding.
* Platform teams: the title line, the visible "None recorded" note and the reference's header
  (no pipeline stores team records yet).

Every formula is planned once (``plan_tech``) so the writer and the recalculation check (B12)
read the same cells.
"""

from __future__ import annotations

from collections.abc import Sequence

from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from exports.config import Column, ExportConfig
from exports.excel.common import Book, Formula, criterion, expected_of, font, text_cell
from exports.models import TechModel
from exports.readers.tech import RANK_LETTERS

# the title line, then the base line, then the rule paragraphs, a gap and the table header
_TITLE_ROW = 1
_BASE_ROW = 2
_NOTES_FIRST = 3
# the stack and teams sheets: title, note, a gap, then the header
_LIST_HEADER = 4


def _rows(model: TechModel) -> dict[str, int]:
    """Row positions of the Platform Ranking blocks (one computation for writer and checker)."""
    header = _NOTES_FIRST + len(model.notes) + 1
    first = header + 1
    last = header + len(model.ranking)
    counts_heading = last + 2
    counts_first = counts_heading + 1
    seen_heading = counts_first + len(RANK_LETTERS) + 1
    return {
        "header": header,
        "first": first,
        "last": max(first, last),
        "counts_heading": counts_heading,
        "counts_first": counts_first,
        "seen_heading": seen_heading,
        "seen_first": seen_heading + 1,
    }


def plan_tech(model: TechModel, cfg: ExportConfig) -> tuple[Formula, ...]:
    """The rank, share and rank-count formula cells with their code-computed values."""
    book = cfg.rules.workbooks.tech
    keys = [c.key for c in book.ranking_columns]
    col = {k: keys.index(k) + 1 for k in keys}
    letter = {k: get_column_letter(n) for k, n in col.items()}
    sheet = book.ranking_sheet
    rows = _rows(model)
    out: list[Formula] = []
    count, share = letter["count"], letter["share"]
    base = f"${share}${_BASE_ROW}"
    for i, r in enumerate(model.ranking, start=rows["first"]):
        rank = (
            f'=IF(AND({share}{i}>=0.15,{count}{i}>=5),"A",'
            f'IF(AND({share}{i}>=0.05,{count}{i}>=3),"B",IF({count}{i}>=2,"C","")))'
        )
        out.append(Formula(sheet, i, col["rank"], rank, r.rank, "label"))
        share_text = f"=IF({base}=0,0,{count}{i}/{base})"
        out.append(Formula(sheet, i, col["share"], share_text, r.share, "percent"))
    rank_span = f"${letter['rank']}${rows['first']}:${letter['rank']}${rows['last']}"
    for j, (x, n) in enumerate(model.rank_counts):
        out.append(
            Formula(sheet, rows["counts_first"] + j, 2, f"=COUNTIF({rank_span},{criterion(x)})", n)
        )
    return tuple(out)


def expected_tech(model: TechModel, cfg: ExportConfig) -> dict[tuple[str, str], int | str | float]:
    """Formula cells and the values they must show (the Platform Ranking's)."""
    return expected_of(plan_tech(model, cfg))


def _title(book: Book, ws: Worksheet, text: str) -> None:
    """The sheet's title line in the theme's title font."""
    name = "Export title"
    theme = book.theme
    book._named(name, font(theme.fonts.title), None, None, theme.column_kinds["label"])
    cell = ws.cell(row=_TITLE_ROW, column=1)
    text_cell(cell, text)
    cell.style = name


def _paragraph(book: Book, ws: Worksheet, row: int, text: str, width: int) -> None:
    """A note paragraph across the table's width, wrapped (the reference merges these rows)."""
    name = "Export paragraph"
    theme = book.theme
    book._named(name, font(theme.fonts.note), None, None, theme.column_kinds["text_long"])
    cell = ws.cell(row=row, column=1)
    text_cell(cell, text)
    cell.style = name
    if width > 1:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=width)


def _filter(book: Book, ws: Worksheet, columns: Sequence[Column], header: int, n: int) -> None:
    if book.theme.autofilter:
        ws.auto_filter.ref = f"A{header}:{get_column_letter(len(columns))}{header + max(n, 0)}"


def _ranking(book: Book, model: TechModel, cfg: ExportConfig) -> None:
    layout = cfg.rules.workbooks.tech
    texts = cfg.rules.sheet_texts.tech
    cols = layout.ranking_columns
    keys = [c.key for c in cols]
    ws = book.sheet(layout.ranking_sheet)
    rows = _rows(model)
    _title(book, ws, model.title)
    book.note(ws, _BASE_ROW, model.base_line)
    base_cell = ws.cell(row=_BASE_ROW, column=keys.index("share") + 1)
    base_cell.value = model.base
    base_cell.style = book._body_style("count")
    for k, note in enumerate(model.notes):
        _paragraph(book, ws, _NOTES_FIRST + k, note, len(cols))
    book.header(ws, cols, row=rows["header"])
    for i, row in enumerate(model.ranking, start=rows["first"]):
        book.body(ws, cols, row.model_dump(), i, "")
    _filter(book, ws, cols, rows["header"], len(model.ranking))
    book.note(ws, rows["counts_heading"], texts.counts_heading)
    for j, (x, n) in enumerate(model.rank_counts):
        at = rows["counts_first"] + j
        book.note(ws, at, texts.rank_label.format(rank=x))
        cell = ws.cell(row=at, column=2)
        cell.value = n
        cell.style = book._body_style("count")
    book.note(ws, rows["seen_heading"], texts.seen_once_heading)
    shown = [c for c in cols if c.key in ("platform", "category", "lane")]
    for i, r in enumerate(model.seen_once, start=rows["seen_first"]):
        values = r.model_dump()
        for c in shown:
            cell = ws.cell(row=i, column=keys.index(c.key) + 1)
            text_cell(cell, str(values[c.key]))
            cell.style = book._body_style(c.kind)
    for f in plan_tech(model, cfg):
        book.formula(f)


def render_tech(model: TechModel, cfg: ExportConfig, title: str) -> tuple[bytes, int]:
    """(xlsx bytes, continuation parts)."""
    layout = cfg.rules.workbooks.tech
    book = Book(cfg)
    _ranking(book, model, cfg)

    ws = book.sheet(layout.sheet)
    _title(book, ws, model.stack_title)
    _paragraph(book, ws, _TITLE_ROW + 1, model.stack_note, len(layout.columns))
    book.header(ws, layout.columns, row=_LIST_HEADER)
    for i, detection in enumerate(model.rows, start=_LIST_HEADER + 1):
        book.body(ws, layout.columns, detection.model_dump(), i, detection.ref)
    if not model.rows:
        book.note(ws, _LIST_HEADER + 1, cfg.rules.none_found.text)
    _filter(book, ws, layout.columns, _LIST_HEADER, len(model.rows))

    teams = book.sheet(layout.teams_sheet)
    _title(book, teams, model.teams_title)
    book.note(teams, _TITLE_ROW + 1, model.teams_note)
    book.header(teams, layout.teams_columns, row=_LIST_HEADER)
    return book.bytes(title, model.as_of), 0
