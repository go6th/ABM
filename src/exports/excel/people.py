"""People List workbook in the shape of the owner's reference People List (master prompt v2
section 9.3; BD, 2026-10-19). The Summary sheet comes first: the title, the paragraphs in the
reference's order (what the list merges and from which searches, the rows removed before it, the
people excluded by title by name, the seniority rule, the judgment note), the Level table with
its Total and Meets seniority rule rows, the Lane table (senior people only, A, B, C and Total)
with its Total row, then the people module's count sentences. The People sheet follows with the
reference's columns; profile links read "Open profile". Layout from ``workbooks.people``; look
from the theme; every formula cell is planned once (``plan_people``) and checked after
recalculation (B12)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from exports.config import Column, ExportConfig
from exports.excel.common import Book, Formula, criterion, expected_of
from exports.models import PeopleModel

# the paragraphs span six columns, as the reference's do (A to F, past the Lane table)
_SPAN = 6


@dataclass(frozen=True)
class _Layout:
    """Row positions of the Summary sheet blocks (one computation for writer and checker)."""

    paragraphs_first: int
    level_header: int
    level_first: int
    level_total: int
    senior_row: int
    lane_header: int
    lane_first: int
    lane_total: int
    sentences_first: int


def _layout(model: PeopleModel) -> _Layout:
    paragraphs_first = 3
    level_header = paragraphs_first + len(model.paragraphs) + 2
    level_first = level_header + 1
    level_total = level_first + len(model.levels)
    senior_row = level_total + 1
    lane_header = senior_row + 2
    lane_first = lane_header + 1
    lane_total = lane_first + len(model.lanes_table)
    return _Layout(
        paragraphs_first,
        level_header,
        level_first,
        level_total,
        senior_row,
        lane_header,
        lane_first,
        lane_total,
        lane_total + 2,
    )


def plan_people(model: PeopleModel, cfg: ExportConfig) -> tuple[_Layout, tuple[Formula, ...]]:
    """The Summary sheet layout and every formula cell with its code-computed value."""
    book = cfg.rules.workbooks.people
    labels = cfg.rules.labels
    keys = [c.key for c in book.columns]
    col = {k: get_column_letter(keys.index(k) + 1) for k in keys}
    last = max(2, len(model.rows) + 1)

    def span(key: str) -> str:
        return f"'{book.sheet}'!${col[key]}$2:${col[key]}${last}"

    lay = _layout(model)
    sheet = book.summary_sheet
    out: list[Formula] = []
    for i, level in enumerate(model.levels):
        row = lay.level_first + i
        label = criterion(level.label)
        out.append(Formula(sheet, row, 2, f"=COUNTIF({span('level')},{label})", level.people))
        out.append(
            Formula(
                sheet,
                row,
                3,
                f"=COUNTIFS({span('level')},{label},{span('priority')},{criterion('A')})",
                level.priority_a,
            )
        )
    if model.levels:
        first, before = lay.level_first, lay.level_total - 1
        out.append(Formula(sheet, lay.level_total, 2, f"=SUM(B{first}:B{before})", len(model.rows)))
        out.append(
            Formula(
                sheet,
                lay.level_total,
                3,
                f"=SUM(C{first}:C{before})",
                sum(x.priority_a for x in model.levels),
            )
        )
    yes = criterion(labels.yes)
    out.append(
        Formula(
            sheet,
            lay.senior_row,
            2,
            f"=COUNTIF({span('meets_seniority')},{yes})",
            model.senior_count,
        )
    )
    for i, lane in enumerate(model.lanes_table):
        row = lay.lane_first + i
        lane_c = criterion(lane.label)
        for j, (letter, n) in enumerate((("A", lane.a), ("B", lane.b), ("C", lane.c)), start=2):
            text = (
                f"=COUNTIFS({span('lanes')},{lane_c},{span('priority')},{criterion(letter)},"
                f"{span('meets_seniority')},{yes})"
            )
            out.append(Formula(sheet, row, j, text, n))
        out.append(Formula(sheet, row, 5, f"=SUM(B{row}:D{row})", lane.total))
    if model.lanes_table:
        first, before = lay.lane_first, lay.lane_total - 1
        totals = (
            sum(x.a for x in model.lanes_table),
            sum(x.b for x in model.lanes_table),
            sum(x.c for x in model.lanes_table),
            sum(x.total for x in model.lanes_table),
        )
        for j, n in enumerate(totals, start=2):
            letter = get_column_letter(j)
            text = f"=SUM({letter}{first}:{letter}{before})"
            out.append(Formula(sheet, lay.lane_total, j, text, n))
    return lay, tuple(out)


def expected_people(
    model: PeopleModel, cfg: ExportConfig
) -> dict[tuple[str, str], int | str | float]:
    """Formula cells and the values they must show (the builder's ``_expected``)."""
    return expected_of(plan_people(model, cfg)[1])


def _links(
    ws: Worksheet, columns: tuple[Column, ...], row: int, values: dict[str, Any], text: str
) -> None:
    """Every link cell shows ``text`` and links to its URL (the reference's "Open profile")."""
    for j, column in enumerate(columns, start=1):
        url = values.get(column.key)
        if column.kind == "url" and url:
            cell = ws.cell(row=row, column=j)
            cell.value = text
            cell.hyperlink = str(url)


def render_people(model: PeopleModel, cfg: ExportConfig, title: str) -> tuple[bytes, int]:
    """(xlsx bytes, continuation parts)."""
    layout = cfg.rules.workbooks.people
    texts = cfg.rules.sheet_texts.people
    book = Book(cfg)
    summary = book.sheet(layout.summary_sheet)
    ws = book.sheet(layout.sheet)
    book.header(ws, layout.columns)
    keys = [c.key for c in layout.columns]
    if book.theme.freeze_header and "name" in keys:
        # the reference keeps the number and the name in view while scrolling right
        ws.freeze_panes = f"{get_column_letter(keys.index('name') + 2)}2"
    for i, row in enumerate(model.rows, start=2):
        values = row.model_dump()
        book.body(ws, layout.columns, values, i, row.ref)
        _links(ws, layout.columns, i, values, texts.link_text)
    if not model.rows:
        book.note(ws, 3, cfg.rules.none_found.text)
    book.finish_table(ws, layout.columns, len(model.rows))

    lay, formulas = plan_people(model, cfg)
    level_cols = (
        Column(key="label", header=texts.level_heading, kind="text_short"),
        Column(key="people", header="People", kind="count"),
        Column(key="priority_a", header="Priority A", kind="count"),
    )
    lane_cols = (
        Column(key="label", header=texts.lane_heading, kind="text_short"),
        Column(key="a", header="A", kind="count"),
        Column(key="b", header="B", kind="count"),
        Column(key="c", header="C", kind="count"),
        Column(key="total", header="Total", kind="count"),
    )
    book.header(summary, level_cols, row=lay.level_header, freeze=False)
    for i, level in enumerate(model.levels):
        book.body(summary, level_cols, level.model_dump(), lay.level_first + i, "")
    book.body(
        summary,
        level_cols,
        {
            "label": texts.total_label,
            "people": len(model.rows),
            "priority_a": sum(x.priority_a for x in model.levels),
        },
        lay.level_total,
        "",
    )
    book.body(
        summary,
        level_cols[:2],
        {"label": texts.senior_label, "people": model.senior_count},
        lay.senior_row,
        "",
    )
    book.header(summary, lane_cols, row=lay.lane_header, freeze=False)
    for i, lane in enumerate(model.lanes_table):
        book.body(summary, lane_cols, lane.model_dump(), lay.lane_first + i, "")
    book.body(
        summary,
        lane_cols,
        {
            "label": texts.total_label,
            "a": sum(x.a for x in model.lanes_table),
            "b": sum(x.b for x in model.lanes_table),
            "c": sum(x.c for x in model.lanes_table),
            "total": sum(x.total for x in model.lanes_table),
        },
        lay.lane_total,
        "",
    )
    # the title and the paragraphs last, so their heights follow the final column widths
    book.title_line(summary, 1, model.title_line or title)
    for k, text in enumerate(model.paragraphs, start=lay.paragraphs_first):
        book.paragraph(summary, k, text, _SPAN)
    for k, sentence in enumerate(model.sentences, start=lay.sentences_first):
        book.note(summary, k, sentence)
    for f in formulas:
        book.formula(f)
    return book.bytes(title, model.as_of), 0
