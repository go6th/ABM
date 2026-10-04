"""Tech Stack and Teams workbook: Detections, Teams (a visible "None recorded" note when no team
data exists) and Counts (formula total, B12)."""

from __future__ import annotations

from exports.config import ExportConfig
from exports.excel.common import Book, counts_sheet
from exports.models import TechModel


def render_tech(model: TechModel, cfg: ExportConfig, title: str) -> tuple[bytes, int]:
    """(xlsx bytes, continuation parts)."""
    layout = cfg.rules.workbooks.tech
    book = Book(cfg)
    ws = book.sheet(layout.sheet)
    book.header(ws, layout.columns)
    for i, row in enumerate(model.rows, start=2):
        book.body(ws, layout.columns, row.model_dump(), i, row.ref)
    if not model.rows:
        book.note(ws, 3, cfg.rules.none_found.text)
    book.finish_table(ws, layout.columns, len(model.rows))
    teams = book.sheet(layout.teams_sheet)
    book.note(teams, 1, model.teams_note)
    counts_sheet(book, layout.counts_sheet, model.counts, layout.sheet, len(model.rows), None, {})
    return book.bytes(title, model.as_of), 0
