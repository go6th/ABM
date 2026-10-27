"""Job Postings workbook, in the order of the owner's reference file (BD_Job_Postings.xlsx):
Summary (title line, explanatory paragraphs, the platform matrix with Total, Closed, Open and one
column per class as COUNTIFS over the Jobs sheet, then the row counts, B12), Jobs (the reference
columns), Counts (the count lines as formulas and the job pipeline's own counts) and Long text
(counted continuation of descriptions over the cell limit, B11)."""

from __future__ import annotations

from dataclasses import dataclass

from openpyxl.utils import get_column_letter

from exports.config import Column, ExportConfig
from exports.excel.common import Book, Formula, counts_sheet, criterion, expected_of
from exports.models import JobsModel


@dataclass(frozen=True)
class _Layout:
    """Summary sheet rows (1-based)."""

    title: int
    paragraphs_first: int
    matrix_header: int
    matrix_first: int
    rows_all: int
    rows_account: int
    rows_other: int


def _layout(model: JobsModel) -> _Layout:
    first = 3
    header = first + len(model.summary_notes) + 1
    rows_all = header + len(model.matrix) + 2
    return _Layout(
        title=1,
        paragraphs_first=first,
        matrix_header=header,
        matrix_first=header + 1,
        rows_all=rows_all,
        rows_account=rows_all + 1,
        rows_other=rows_all + 2,
    )


def _wildcard(name: str) -> str:
    """A COUNTIF criterion for a cell that contains ``name`` (any case)."""
    return f'"*{name.replace("~", "~~").replace("*", "~*").replace("?", "~?")}*"'


def plan_jobs(model: JobsModel, cfg: ExportConfig) -> tuple[Formula, ...]:
    """The Summary sheet's formula cells with their code-computed values: the platform matrix
    (Total, Closed, Open, then one column per class) and the three row counts (the count lines
    of the Counts sheet are written by ``counts_sheet`` and checked through ``formula``)."""
    book = cfg.rules.workbooks.jobs
    labels = cfg.rules.labels
    keys = [c.key for c in book.columns]
    col = {k: get_column_letter(keys.index(k) + 1) for k in keys}
    last = max(2, len(model.rows) + 1)

    def span(key: str) -> str:
        return f"'{book.sheet}'!${col[key]}$2:${col[key]}${last}"

    platforms, classes, status = span("platforms"), span("posting_class"), span("status")
    refs = f"'{book.sheet}'!{col['ref']}2:{col['ref']}{last}"
    closed_word = criterion(labels.posting_status["closed"])
    open_word = criterion(labels.posting_status["open"])
    lay = _layout(model)
    sheet = book.summary_sheet
    out: list[Formula] = []
    for i, m in enumerate(model.matrix):
        row = lay.matrix_first + i
        if m.platform == book.matrix_all_rows:
            cells = [
                (f"=COUNTA({refs})", m.rows),
                (f"=COUNTIF({status},{closed_word})", m.closed),
                (f"=COUNTIF({status},{open_word})", m.open),
                *(
                    (f"=COUNTIF({classes},{criterion(label)})", n)
                    for label, n in zip(model.class_labels, m.by_class, strict=True)
                ),
            ]
        else:
            name = f"{platforms},{_wildcard(m.platform)}"
            cells = [
                (f"=COUNTIF({name})", m.rows),
                (f"=COUNTIFS({name},{status},{closed_word})", m.closed),
                (f"=COUNTIFS({name},{status},{open_word})", m.open),
                *(
                    (f"=COUNTIFS({name},{classes},{criterion(label)})", n)
                    for label, n in zip(model.class_labels, m.by_class, strict=True)
                ),
            ]
        out.extend(Formula(sheet, row, 2 + j, text, n) for j, (text, n) in enumerate(cells))
    account = sum(1 for r in model.rows if r.entity == model.account)
    entity = f"COUNTIF({span('entity')},{criterion(model.account)})"
    out.append(Formula(sheet, lay.rows_all, 2, f"=COUNTA({refs})", len(model.rows)))
    out.append(Formula(sheet, lay.rows_account, 2, f"={entity}", account))
    out.append(
        Formula(sheet, lay.rows_other, 2, f"=COUNTA({refs})-{entity}", len(model.rows) - account)
    )
    return tuple(out)


def expected_jobs(model: JobsModel, cfg: ExportConfig) -> dict[tuple[str, str], int | str | float]:
    """Formula cells and the values they must show: the count lines and the Summary sheet."""
    sheet = cfg.rules.workbooks.jobs.counts_sheet
    lines: dict[tuple[str, str], int | str | float] = {
        (sheet, f"B{i}"): line.value
        for i, line in enumerate(model.counts, start=2)
        if line.formula != "none"
    }
    return {**lines, **expected_of(plan_jobs(model, cfg))}


def render_jobs(model: JobsModel, cfg: ExportConfig, title: str) -> tuple[bytes, int]:
    """(xlsx bytes, continuation parts)."""
    layout = cfg.rules.workbooks.jobs
    texts = cfg.rules.sheet_texts.jobs
    book = Book(cfg, long_text_sheet=layout.long_text_sheet)
    summary = book.sheet(layout.summary_sheet)  # first, as in the reference file
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
        count_col=get_column_letter(keys.index("ref") + 1),
    )
    cws = book.wb[layout.counts_sheet]
    for k, sentence in enumerate(model.sentences, start=len(model.counts) + 3):
        book.note(cws, k, sentence)

    lay = _layout(model)
    matrix_cols = (
        Column(key="platform", header=texts.matrix_heading, kind="text_short"),
        Column(key="rows", header=texts.total, kind="count"),
        Column(key="closed", header=texts.closed, kind="count"),
        Column(key="open", header=texts.open, kind="count"),
        *(
            Column(key=f"class_{j}", header=label, kind="count")
            for j, label in enumerate(model.class_labels)
        ),
    )
    book.header(summary, matrix_cols, row=lay.matrix_header, freeze=False)
    for i, m in enumerate(model.matrix):
        values = {"platform": m.platform, "rows": m.rows, "closed": m.closed, "open": m.open}
        values.update({f"class_{j}": n for j, n in enumerate(m.by_class)})
        book.body(summary, matrix_cols, values, lay.matrix_first + i, "")
    line_cols = matrix_cols[:2]
    for line_row, label in (
        (lay.rows_all, texts.rows_all),
        (lay.rows_account, texts.rows_account.format(account=model.account)),
        (lay.rows_other, texts.rows_other),
    ):
        book.body(summary, line_cols, {"platform": label, "rows": 0}, line_row, "")
    for f in plan_jobs(model, cfg):
        book.formula(f)
    book.title_line(summary, lay.title, model.summary_title or model.account)
    for k, text in enumerate(model.summary_notes, start=lay.paragraphs_first):
        book.paragraph(summary, k, text, len(matrix_cols))
    book.write_long_text()
    parts = book.long.parts if book.long else 0
    return book.bytes(title, model.as_of), parts
