"""The Job Postings workbook: a Summary sheet (notes and the per platform COUNTIFS over the
Jobs sheet, the account's positions only) and the Jobs sheet, one row per posting, rendered
from a bundle exactly as the reference wrote it."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment

from parity.bundle import AccountBundle
from parity.excel import (
    body_font,
    link_font,
    style_header,
    style_table,
    thin_border,
    title_font,
)
from parity.rules import CLASSES
from parity.tables import BY_ID
from parity.text import clean_posting

# the header row of the Jobs sheet (parity.tables, the one definition the web shares)
HEADER = BY_ID["jobs"].headers
WIDTHS = [12, 44, 18, 20, 9, 11, 11, 26, 11, 26, 50, 70, 60, 13]
WRAPPED_COLUMNS = {2, 8, 11, 12}
SUMMARY_HEADER = ["Total", "Closed", "Open", *CLASSES]


def other_rows_label(bundle: AccountBundle) -> str:
    """The Summary row that counts rows which are not the account's positions."""
    label = bundle.prose.workbook_notes.jobs_other_entities_label
    return label or f"Rows that are not {bundle.meta.account_short} positions"


def render_jobs(bundle: AccountBundle, path: Path) -> Path:
    """Write ``{prefix}_Job_Postings.xlsx`` to ``path`` and return it."""
    prefix = bundle.meta.account_short
    rows = sorted(bundle.jobs_rows, key=lambda r: r.first_seen, reverse=True)
    wb = Workbook()
    ws = wb.worksheets[0]
    ws.title = "Jobs"
    ws.append(HEADER)
    for i, r in enumerate(rows, 1):
        ws.append(
            [
                r.requisition or "",
                clean_posting(r.title),
                ", ".join(r.platforms),
                r.cls,
                r.status,
                r.first_seen,
                r.last_seen,
                clean_posting(r.location),
                r.seniority or "",
                r.entity,
                r.what,
                clean_posting(r.platform_sentences)[:3000],
                clean_posting(r.description)[:32000],
                "Open posting",
            ]
        )
        c = ws.cell(row=i + 1, column=14)
        c.hyperlink = r.url
        c.font = link_font()
    style_header(ws[1])
    # the formula ranges end at the last row, or at the first empty row when the sheet has none
    # (a range ending on the header would count it once LibreOffice re-orders it)
    last = len(rows) + 1
    # (LibreOffice normalises a one-cell range to the cell, which the workbook check reads as a
    # changed formula, so the range always spans at least two rows)
    end = max(last, 3)
    for row in ws.iter_rows(min_row=2, max_row=last):
        for c in row:
            if not c.hyperlink:
                c.font = body_font()
            c.alignment = Alignment(vertical="top", wrap_text=(c.column in WRAPPED_COLUMNS))
            c.border = thin_border()
    for col, w in zip("ABCDEFGHIJKLMN", WIDTHS, strict=True):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:N{end}"

    s = wb.create_sheet("Summary", 0)
    t = "Jobs"
    wd = bundle.meta.window_display
    s["A1"] = f"{prefix} platform job postings, last 12 months (first seen {wd.from_} to {wd.to})"
    s["A1"].font = title_font()
    notes = bundle.prose.workbook_notes.jobs
    for k, n in enumerate(notes):
        c = s.cell(row=3 + k, column=1, value=n)
        c.font = body_font()
        c.alignment = Alignment(wrap_text=True, vertical="top")
        s.merge_cells(start_row=3 + k, start_column=1, end_row=3 + k, end_column=8)
        s.row_dimensions[3 + k].height = 46
    r0 = 3 + len(notes) + 2
    s.cell(row=r0, column=1, value=f"Platform (named anywhere), {prefix} positions only")
    for j, h in enumerate(SUMMARY_HEADER):
        s.cell(row=r0, column=2 + j, value=h)
    lanes = bundle.meta.lanes
    for k, pl in enumerate(lanes):
        row_n = r0 + 1 + k
        s.cell(row=row_n, column=1, value=pl)
        base = f'{t}!$C$2:$C${end},"*{pl}*",{t}!$J$2:$J${end},"{prefix}*"'
        s.cell(row=row_n, column=2, value=f"=COUNTIFS({base})")
        s.cell(row=row_n, column=3, value=f'=COUNTIFS({base},{t}!$E$2:$E${end},"Closed")')
        s.cell(row=row_n, column=4, value=f'=COUNTIFS({base},{t}!$E$2:$E${end},"Open")')
        for j, cl in enumerate(CLASSES):
            s.cell(row=row_n, column=5 + j, value=f'=COUNTIFS({base},{t}!$D$2:$D${end},"{cl}")')
    rr = r0 + len(lanes) + 2
    s.cell(row=rr, column=1, value="Rows on the Jobs sheet (all entities)")
    s.cell(row=rr, column=2, value=f"=COUNTA({t}!$B$2:$B${end})")
    s.cell(row=rr + 1, column=1, value=f"Rows that are {prefix} positions")
    s.cell(row=rr + 1, column=2, value=f'=COUNTIF({t}!$J$2:$J${end},"{prefix}*")')
    s.cell(row=rr + 2, column=1, value=other_rows_label(bundle))
    s.cell(
        row=rr + 2,
        column=2,
        value=(
            f'=COUNTIF({t}!$J$2:$J${end},"{prefix}*")*0+COUNTA({t}!$B$2:$B${end})'
            f'-COUNTIF({t}!$J$2:$J${end},"{prefix}*")'
        ),
    )
    style_table(s, r0, rr + 2, set())
    style_header(s[r0], wrap=False)
    s.column_dimensions["A"].width = 52
    for col in "BCDEFGH":
        s.column_dimensions[col].width = 18
    wb.save(path)
    return path
