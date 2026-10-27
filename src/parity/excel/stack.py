"""The Tech Stack and Teams workbook: Platform Ranking (first), Confirmed stack and Platform
teams, rendered from a bundle exactly as the reference wrote them (the ranking sheet was added
by a second script, so its fonts carry no explicit size and its title no colour)."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import Rule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.styles.differential import DifferentialStyle
from openpyxl.worksheet.worksheet import Worksheet

from parity.bundle import AccountBundle
from parity.excel import FONT, body_font, navy_fill, style_header, thin_border, title_font
from parity.tables import BY_ID
from parity.text import clean_cell

# the header rows (parity.tables, the one definition the web shares)
STACK_HEADER = BY_ID["stack"].headers
STACK_WIDTHS = [26, 40, 34, 18, 26]
TEAMS_HEADER = BY_ID["teams"].headers
TEAMS_WIDTHS = [40, 30, 10, 12, 12, 70, 50, 40]
RANK_HEADER = BY_ID["ranking"].headers
RANK_WIDTHS = [8, 30, 26, 22, 12, 10, 10, 12, 14, 14]
RANK_FILLS = {"A": "C6EFCE", "B": "FFEB9C", "C": "E7E6E6"}
BASE_LABEL = "Postings in the last 12 months that name at least one platform (the base):"
HEADER_ROW = 8


def _note_block(ws: Worksheet, title: str, note: str, last_col: str) -> None:
    ws["A1"] = title
    ws["A1"].font = title_font()
    ws["A2"] = note
    ws["A2"].font = Font(name=FONT, size=9, italic=True)
    ws.merge_cells(f"A2:{last_col}2")
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[2].height = 40
    ws.append([])


def _confirmed_stack(wb: Workbook, bundle: AccountBundle) -> None:
    notes = bundle.prose.workbook_notes.stack
    ws = wb.create_sheet("Confirmed stack")
    _note_block(ws, notes.confirmed_title, notes.confirmed_note, "E")
    ws.append(STACK_HEADER)
    style_header(ws[4])
    for row in bundle.stack_rows:
        ws.append(
            [
                row.business_function,
                row.category,
                clean_cell(row.technology),
                row.postings,
                row.lane,
            ]
        )
    n = len(bundle.stack_rows)
    for r in ws.iter_rows(min_row=5, max_row=4 + n):
        for c in r:
            c.font = body_font()
            c.border = thin_border()
    for col, w in zip("ABCDE", STACK_WIDTHS, strict=True):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:E{4 + n}"


def _platform_teams(wb: Workbook, bundle: AccountBundle) -> None:
    notes = bundle.prose.workbook_notes.stack
    ws = wb.create_sheet("Platform teams")
    _note_block(ws, notes.teams_title, notes.teams_note, "H")
    ws.append(TEAMS_HEADER)
    style_header(ws[4])
    for row in bundle.team_rows:
        ws.append(
            [
                clean_cell(row.team),
                clean_cell(row.lane_technologies),
                row.postings,
                row.first_activity,
                row.last_activity,
                clean_cell(row.technologies),
                clean_cell(row.locations),
                clean_cell(row.job_functions),
            ]
        )
    m = len(bundle.team_rows)
    for r in ws.iter_rows(min_row=5, max_row=4 + m):
        for c in r:
            c.font = body_font()
            c.border = thin_border()
            c.alignment = Alignment(wrap_text=True, vertical="top")
    for col, w in zip("ABCDEFGH", TEAMS_WIDTHS, strict=True):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A5"


def _platform_ranking(wb: Workbook, bundle: AccountBundle) -> None:
    ranking = bundle.ranking
    ws = wb.create_sheet("Platform Ranking", 0)
    ws["A1"] = f"{bundle.meta.account_short}: platforms seen in job postings, ranked A, B, C"
    ws["A1"].font = Font(name=FONT, bold=True, size=14)
    ws["A2"] = BASE_LABEL
    ws["A2"].font = Font(name=FONT)
    ws["F2"] = ranking.base
    ws["F2"].font = Font(name=FONT, bold=True)
    for i, n in enumerate(bundle.prose.workbook_notes.stack.ranking_notes):
        c = ws.cell(row=3 + i, column=1, value=n)
        c.font = Font(name=FONT, italic=True, size=9)
        ws.merge_cells(start_row=3 + i, start_column=1, end_row=3 + i, end_column=10)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[3 + i].height = 28
    h = HEADER_ROW
    for col, name in enumerate(RANK_HEADER, 1):
        c = ws.cell(row=h, column=col, value=name)
        c.font = Font(name=FONT, bold=True, color="FFFFFF")
        c.fill = navy_fill()
        c.alignment = Alignment(wrap_text=True, vertical="center")
    r = h
    for rec in ranking.rows:
        r += 1
        ws.cell(row=r, column=2, value=clean_cell(rec.platform))
        ws.cell(row=r, column=3, value=rec.category)
        ws.cell(row=r, column=4, value=rec.lane)
        ws.cell(row=r, column=5, value=rec.count)
        ws.cell(row=r, column=6, value=f"=IF($F$2=0,0,E{r}/$F$2)").number_format = "0%"
        ws.cell(row=r, column=7, value=rec.open)
        ws.cell(row=r, column=8, value=rec.last_seen)
        ws.cell(row=r, column=9, value=rec.in_stack)
        ws.cell(row=r, column=10, value=rec.checked)
        ws.cell(
            row=r,
            column=1,
            value=(
                f'=IF(AND(F{r}>=0.15,E{r}>=5),"A",IF(AND(F{r}>=0.05,E{r}>=3),"B",'
                f'IF(E{r}>=2,"C","")))'
            ),
        )
        for col in range(1, 11):
            c = ws.cell(row=r, column=col)
            c.font = Font(name=FONT, bold=(col == 1))
            c.border = thin_border()
    last_row = r
    if ranking.rows:
        for grade, colour in RANK_FILLS.items():
            ws.conditional_formatting.add(
                f"A{h + 1}:A{last_row}",
                Rule(
                    type="expression",
                    formula=[f'$A{h + 1}="{grade}"'],
                    dxf=DifferentialStyle(fill=PatternFill("solid", fgColor=colour)),
                ),
            )
    r = last_row + 2
    ws.cell(row=r, column=1, value="Counts").font = Font(name=FONT, bold=True)
    # one ranked row (or none) counts the single cell, as LibreOffice normalises a one-cell
    # range on re-save and the workbook check compares the formula text
    rank_range = f"$A${h + 1}:$A${last_row}" if last_row > h + 1 else f"$A${h + 1}"
    for grade in "ABC":
        r += 1
        ws.cell(row=r, column=1, value=f"Rank {grade}").font = Font(name=FONT)
        ws.cell(row=r, column=2, value=f'=COUNTIF({rank_range},"{grade}")').font = Font(name=FONT)
    r += 2
    ws.cell(row=r, column=1, value="Seen once (not ranked)").font = Font(name=FONT, bold=True)
    for once in ranking.seen_once:
        r += 1
        ws.cell(row=r, column=2, value=clean_cell(once.platform)).font = Font(name=FONT)
        ws.cell(row=r, column=3, value=once.category).font = Font(name=FONT)
        ws.cell(row=r, column=4, value=once.lane).font = Font(name=FONT)
    if not ranking.seen_once:
        ws.cell(row=r + 1, column=2, value="None").font = Font(name=FONT)
    for letter, w in zip("ABCDEFGHIJ", RANK_WIDTHS, strict=True):
        ws.column_dimensions[letter].width = w
    ws.freeze_panes = f"A{h + 1}"
    ws.auto_filter.ref = f"A{h}:J{max(last_row, h)}"


def render_stack(bundle: AccountBundle, path: Path) -> Path:
    """Write ``{prefix}_Tech_Stack_and_Teams.xlsx`` to ``path`` and return it."""
    wb = Workbook()
    wb.remove(wb.worksheets[0])
    _confirmed_stack(wb, bundle)
    _platform_teams(wb, bundle)
    _platform_ranking(wb, bundle)
    wb.save(path)
    return path
