"""People List workbook: the People sheet and the Summary sheet (counts by status and reason,
never names). Layout from ``workbooks.people``; look from the theme."""

from __future__ import annotations

from exports.config import Column, ExportConfig
from exports.excel.common import Book
from exports.models import PeopleModel


def render_people(model: PeopleModel, cfg: ExportConfig, title: str) -> tuple[bytes, int]:
    """(xlsx bytes, continuation parts)."""
    layout = cfg.rules.workbooks.people
    book = Book(cfg)
    ws = book.sheet(layout.sheet)
    book.header(ws, layout.columns)
    for i, row in enumerate(model.rows, start=2):
        book.body(ws, layout.columns, row.model_dump(), i, row.ref)
    if not model.rows:
        book.note(ws, 3, cfg.rules.none_found.text)
    book.finish_table(ws, layout.columns, len(model.rows))

    summary = book.sheet(layout.summary_sheet)
    cols = (
        Column(key="status", header="Status", kind="label"),
        Column(key="reason", header="Reason", kind="text_short"),
        Column(key="count", header="People", kind="count"),
    )
    book.header(summary, cols)
    for i, line in enumerate(model.summary, start=2):
        book.body(summary, cols, line.model_dump(), i, "")
    listed_row = len(model.summary) + 2
    book.body(
        summary,
        cols,
        {"status": "Listed on the People sheet", "reason": "", "count": model.in_scope_listed},
        listed_row,
        "",
    )
    summary.cell(
        row=listed_row, column=3
    ).value = f"=COUNTA('{layout.sheet}'!A2:A{max(2, len(model.rows) + 1)})"
    for k, sentence in enumerate(model.sentences, start=listed_row + 2):
        book.note(summary, k, sentence)
    book.finish_table(summary, cols, len(model.summary) + 1)
    return book.bytes(title, model.as_of), 0
