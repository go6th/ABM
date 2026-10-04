"""Workbook mechanics shared by the three workbooks (openpyxl).

Every style value comes from the theme (``ExcelTheme``): fonts, fills, borders, column widths and
number formats by column kind, freeze panes, filters and print setup. No literal style value
appears here (static test).

Cells are written injection-safe (B11): every text cell is forced to a string cell, so text such as
``=HYPERLINK(...)`` or ``@SUM(...)`` is stored as text and never as a formula; text is sanitised
by the mapping step. Text longer than the configured cell limit continues on a "Long text" sheet:
the main cell holds the first part and a pointer, every continuation row names its part number,
and the parts joined give the text back exactly (counted in the manifest, never silently cut).
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from openpyxl import Workbook
from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.styles import Alignment, Border, Font, NamedStyle, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.indexed_list import IndexedList
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.worksheet.worksheet import Worksheet

from exports.config import Column, ColumnKind, ColumnStyle, ExcelTheme, ExportConfig
from exports.config import Font as FontSpec
from exports.ooxml import normalise_package
from exports.policy import split_for_cell

# A continuation pointer takes at most this many characters of the main cell.
POINTER_RESERVE = 160


def font(spec: FontSpec) -> Font:
    """openpyxl font from a theme font."""
    return Font(
        name=spec.name, size=spec.size_pt, bold=spec.bold, italic=spec.italic, color=spec.color
    )


def fill(color: str) -> PatternFill:
    """Solid fill from a theme colour."""
    return PatternFill(fill_type="solid", start_color=color, end_color=color)


def border(theme: ExcelTheme) -> Border:
    """Thin border in the theme colour on all four sides."""
    side = Side(style="thin", color=theme.border_color)
    return Border(left=side, right=side, top=side, bottom=side)


def text_cell(cell: Cell | MergedCell, value: str) -> None:
    """Write ``value`` as a string cell, never a formula (B11)."""
    cell.value = value
    cell.data_type = "s"


@dataclass
class LongText:
    """Continuation rows collected while writing (written to the Long text sheet at the end)."""

    sheet_name: str
    rows: list[tuple[str, str, int, int, str]] = field(default_factory=list)

    @property
    def parts(self) -> int:
        """Continuation parts written (part 2 onwards), for the manifest."""
        return len(self.rows)


class Book:
    """One workbook built from the theme; ``bytes()`` returns the normalised package."""

    def __init__(self, cfg: ExportConfig, long_text_sheet: str | None = None) -> None:
        self.cfg = cfg
        self.theme = cfg.theme.excel
        self.wb = Workbook()
        # The workbook default font (font 0 and the Normal style) comes from the theme too, so a
        # theme change changes every font in the file (openpyxl 3.1.5 seeds Calibri 11 here).
        body = font(self.theme.fonts.body)
        self.wb._fonts = IndexedList([body])  # type: ignore[attr-defined]
        self.wb._named_styles["Normal"].font = body  # type: ignore[attr-defined]
        self._first = True
        self.long = LongText(long_text_sheet) if long_text_sheet else None
        self._styles: set[str] = set()
        # every named style registered up front in a fixed order, so style indices (and the
        # stylesheet) are the same for every workbook whatever its content (the same look)
        self._header_style()
        for kind in self.theme.column_kinds:
            self._body_style(kind)
        self._note_style()
        self.limit = cfg.rules.limits.excel_cell_chars

    def sheet(self, title: str) -> Worksheet:
        """A new sheet with the theme's print setup (the default sheet is reused once)."""
        ws = self.wb.active if self._first else self.wb.create_sheet()
        assert ws is not None
        self._first = False
        ws.title = title
        p = self.theme.print
        ws.page_setup.orientation = p.orientation
        ws.page_setup.paperSize = ws.PAPERSIZE_LETTER
        if p.fit_to_width:
            ws.page_setup.fitToWidth = 1
            ws.page_setup.fitToHeight = 0
            ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
        return ws

    def header(self, ws: Worksheet, columns: Sequence[Column], row: int = 1) -> None:
        """Header row, widths and number formats by column kind, freeze and filter."""
        style = self._header_style()
        for j, col in enumerate(columns, start=1):
            cell = ws.cell(row=row, column=j)
            text_cell(cell, col.header)
            cell.style = style
            ws.column_dimensions[get_column_letter(j)].width = self.theme.column_kinds[
                col.kind
            ].width
        if self.theme.freeze_header:
            ws.freeze_panes = f"A{row + 1}"

    def finish_table(self, ws: Worksheet, columns: Sequence[Column], n_rows: int) -> None:
        """Filter over the header and data rows (when configured)."""
        if self.theme.autofilter:
            last = get_column_letter(len(columns))
            ws.auto_filter.ref = f"A1:{last}{max(1, n_rows + 1)}"

    def body(
        self, ws: Worksheet, columns: Sequence[Column], values: dict[str, Any], row: int, ref: str
    ) -> None:
        """One data row: each value styled by its column kind; long text continues (counted)."""
        for j, col in enumerate(columns, start=1):
            cell = ws.cell(row=row, column=j)
            value = values.get(col.key)
            if value is None:
                text_cell(cell, "")
            elif isinstance(value, bool):
                text_cell(cell, "Yes" if value else "No")
            elif isinstance(value, date | int):
                cell.value = value
            else:
                text_cell(cell, self._fit(str(value), ref, col.header))
            cell.style = self._body_style(col.kind)

    def _fit(self, text: str, ref: str, header: str) -> str:
        if len(text) <= self.limit:
            return text
        if self.long is None:
            raise ValueError("text over the cell limit in a sheet without a Long text sheet")
        parts = split_for_cell(text, self.limit - POINTER_RESERVE)
        n = len(parts)
        for k, part in enumerate(parts[1:], start=2):
            self.long.rows.append((ref, header, k, n, part))
        return (
            f"{parts[0]} [continued: part 1 of {n}; parts 2 to {n} are on the "
            f"{self.long.sheet_name} sheet under {ref}]"
        )

    def write_long_text(self) -> None:
        """The Long text sheet: Ref, column, part, of, text (always present when configured)."""
        if self.long is None:
            return
        ws = self.sheet(self.long.sheet_name)
        cols = (
            Column(key="ref", header="Ref", kind="ref"),
            Column(key="column", header="Column", kind="label"),
            Column(key="part", header="Part", kind="count"),
            Column(key="of", header="Of", kind="count"),
            Column(key="text", header="Text", kind="text_long"),
        )
        self.header(ws, cols)
        for i, (ref, header, k, n, part) in enumerate(self.long.rows, start=2):
            self.body(
                ws, cols, {"ref": ref, "column": header, "part": k, "of": n, "text": part}, i, ref
            )
        self.finish_table(ws, cols, len(self.long.rows))

    def note(self, ws: Worksheet, row: int, text: str) -> None:
        """A visible note line (for example "None found")."""
        cell = ws.cell(row=row, column=1)
        text_cell(cell, text)
        cell.style = self._note_style()

    # Named styles: one per role and column kind, registered once per workbook and assigned by
    # name (per-cell style objects are re-hashed by openpyxl on every assignment: the hot path).

    def _named(
        self,
        name: str,
        f: Font,
        filled: PatternFill | None,
        b: Border | None,
        kind: ColumnStyle,
        wrap: bool | None = None,
    ) -> str:
        if name not in self._styles:
            style = NamedStyle(
                name=name,
                font=f,
                number_format=kind.number_format,
                alignment=Alignment(wrap_text=kind.wrap if wrap is None else wrap, vertical="top"),
            )
            if filled is not None:
                style.fill = filled
            if b is not None:
                style.border = b
            self.wb.add_named_style(style)
            self._styles.add(name)
        return name

    def _note_style(self) -> str:
        name = "Export note"
        if name in self._styles:
            return name
        return self._named(
            name, font(self.theme.fonts.note), None, None, self.theme.column_kinds["label"]
        )

    def _header_style(self) -> str:
        name = "Export header"
        if name in self._styles:  # checked first: building the parts is the costly step
            return name
        return self._named(
            name,
            font(self.theme.fonts.header),
            fill(self.theme.header_fill),
            border(self.theme),
            self.theme.column_kinds["label"],
            wrap=True,
        )

    def _body_style(self, kind: ColumnKind) -> str:
        name = f"Export body {kind}"
        if name in self._styles:
            return name
        return self._named(
            name,
            font(self.theme.fonts.body),
            None,
            border(self.theme),
            self.theme.column_kinds[kind],
        )

    def bytes(self, title: str, as_of: date) -> bytes:
        """The workbook as a normalised, deterministic package (B10)."""
        buf = io.BytesIO()
        self.wb.save(buf)
        props = self.cfg.rules.document_properties
        z = self.cfg.theme.zip
        return normalise_package(
            buf.getvalue(),
            title=title,
            author=props.author,
            company=props.company,
            as_of=as_of,
            entry_time=z.entry_time,
            compression=z.compression,
        )


def counts_sheet(
    book: Book,
    title: str,
    lines: Sequence[Any],
    data_sheet: str,
    n_rows: int,
    status_col: str | None,
    status_words: dict[str, str],
) -> None:
    """Counts sheet: label, value, base. Lines marked ``rows``/``open``/``closed`` are formulas
    over the data sheet (B12); the verification recalculates them against the code counts."""
    ws = book.sheet(title)
    cols = (
        Column(key="label", header="Count", kind="text_short"),
        Column(key="value", header="Value", kind="count"),
        Column(key="base", header="Base", kind="text_short"),
    )
    book.header(ws, cols)
    last = n_rows + 1
    for i, line in enumerate(lines, start=2):
        book.body(ws, cols, {"label": line.label, "value": line.value, "base": line.base}, i, "")
        cell = ws.cell(row=i, column=2)
        if line.formula == "rows":
            cell.value = f"=COUNTA('{data_sheet}'!A2:A{max(2, last)})"
        elif line.formula in ("open", "closed") and status_col:
            word = status_words[line.formula].replace('"', '""')
            span = f"'{data_sheet}'!{status_col}2:{status_col}{max(2, last)}"
            cell.value = f'=COUNTIF({span},"{word}")'
    book.finish_table(ws, cols, len(lines))
