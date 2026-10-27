"""The three workbook renderers (``people``, ``jobs``, ``stack``) and their shared styles:
Arial, the navy header, the thin grey border, as the reference workbooks were written."""

from __future__ import annotations

from collections.abc import Iterable

from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.worksheet import Worksheet

FONT = "Arial"
NAVY_HEX = "1F3A5F"
LINK_HEX = "1155CC"
THIN_HEX = "BFC5CC"


def navy_fill() -> PatternFill:
    """The header fill."""
    return PatternFill("solid", fgColor=NAVY_HEX)


def thin_border() -> Border:
    """The thin grey border on every side."""
    side = Side(style="thin", color=THIN_HEX)
    return Border(top=side, bottom=side, left=side, right=side)


def title_font() -> Font:
    """The sheet title font (14, bold, navy)."""
    return Font(name=FONT, size=14, bold=True, color=NAVY_HEX)


def body_font(bold: bool = False) -> Font:
    """The 10 point body font."""
    return Font(name=FONT, size=10, bold=bold)


def link_font() -> Font:
    """The hyperlink font (blue, underlined)."""
    return Font(name=FONT, size=10, color=LINK_HEX, underline="single")


def style_header(cells: Iterable[Cell | MergedCell], wrap: bool = True) -> None:
    """White bold text on navy, vertically centred, as every header row is styled."""
    for c in cells:
        if c.value is None:
            continue
        c.font = Font(name=FONT, size=10, bold=True, color="FFFFFF")
        c.fill = navy_fill()
        c.alignment = Alignment(vertical="center", wrap_text=wrap)


def style_table(ws: Worksheet, first_row: int, last_row: int, bold_rows: set[int]) -> None:
    """Body font and border on every filled cell of a Summary table."""
    for row in ws.iter_rows(min_row=first_row, max_row=last_row):
        for c in row:
            if c.value is None:
                continue
            c.font = body_font(bold=c.row in bold_rows)
            c.border = thin_border()
