"""Job Postings workbook: Postings, Counts (with formula totals, B12) and Long text (counted
continuation of descriptions over the cell limit, B11)."""

from __future__ import annotations

from openpyxl.utils import get_column_letter

from exports.config import ExportConfig
from exports.excel.common import Book, counts_sheet
from exports.models import JobsModel


def render_jobs(model: JobsModel, cfg: ExportConfig, title: str) -> tuple[bytes, int]:
    """(xlsx bytes, continuation parts)."""
    layout = cfg.rules.workbooks.jobs
    book = Book(cfg, long_text_sheet=layout.long_text_sheet)
    ws = book.sheet(layout.sheet)
    book.header(ws, layout.columns)
    for i, row in enumerate(model.rows, start=2):
        book.body(ws, layout.columns, row.model_dump(), i, row.ref)
    if not model.rows:
        book.note(ws, 3, cfg.rules.none_found.text)
    book.finish_table(ws, layout.columns, len(model.rows))
    keys = [c.key for c in layout.columns]
    status_col = get_column_letter(keys.index("status") + 1)
    counts_sheet(
        book,
        layout.counts_sheet,
        model.counts,
        layout.sheet,
        len(model.rows),
        status_col,
        cfg.rules.labels.posting_status,
    )
    cws = book.wb[layout.counts_sheet]
    for k, sentence in enumerate(model.sentences, start=len(model.counts) + 3):
        book.note(cws, k, sentence)
    book.write_long_text()
    parts = book.long.parts if book.long else 0
    return book.bytes(title, model.as_of), parts
