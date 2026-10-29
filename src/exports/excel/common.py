"""Workbook mechanics shared by the three workbooks (openpyxl).

Every style value comes from the theme (``ExcelTheme``): fonts, fills, borders, column widths and
number formats by column kind (a column may override its width, wrapping and alignment), freeze
panes, filters and print setup. No literal style value appears here (static test).

Cells are written injection-safe (B11): every text cell is forced to a string cell, so text such as
``=HYPERLINK(...)`` or ``@SUM(...)`` is stored as text and never as a formula; text is sanitised
by the mapping step. Text longer than the configured cell limit continues on a "Long text" sheet:
the main cell holds the first part and a pointer, every continuation row names its part number,
and the parts joined give the text back exactly (counted in the manifest, never silently cut).

Styles are named styles registered up front, one per look (role, column kind, wrap, alignment,
weight, fill), and every registered look is also entered in the workbook's cell-format table at
registration time, so the stylesheet (and the "same look" fingerprint) never depends on which
rows an account happens to have: a fill applied to one Priority cell or to none leaves the
stylesheet unchanged.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from copy import copy
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Literal

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
# A wrapped paragraph's row height: lines times the font size times this line spacing; the
# characters are padded by WRAP_SLACK for words that do not fill a line (Excel does not size a
# merged row on its own).
LINE_SPACING = 1.3
WRAP_SLACK = 1.15

Role = Literal["body", "link", "header", "title", "note", "paragraph"]
_PLAIN_SHEET_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_CELL_LIKE = re.compile(r"^[A-Za-z]{1,3}[0-9]+$")


@dataclass(frozen=True)
class Formula:
    """One formula cell a workbook recomputes (B12): where it goes, its text, the value computed
    in code it must equal after recalculation, and the column kind that styles it."""

    sheet: str
    row: int
    column: int
    text: str
    expected: int | str | float
    kind: ColumnKind = "count"

    @property
    def coordinate(self) -> str:
        """``B7``."""
        return f"{get_column_letter(self.column)}{self.row}"


def expected_of(plan: Sequence[Formula]) -> dict[tuple[str, str], int | str | float]:
    """``{(sheet, coordinate): expected}`` of a formula plan (what the recalculation checks)."""
    return {(f.sheet, f.coordinate): f.expected for f in plan}


def criterion(text: str) -> str:
    """A COUNTIF criterion for an exact text: quotes doubled; a leading comparison sign or
    wildcard would change the meaning, so the text is prefixed with ``=`` when it has one."""
    safe = text.replace('"', '""')
    if safe[:1] in ("=", "<", ">") or any(ch in safe for ch in "*?~"):
        safe = "=" + safe.replace("~", "~~").replace("*", "~*").replace("?", "~?")
    return f'"{safe}"'


def prefix_criterion(text: str) -> str:
    """A COUNTIF criterion matching every cell that STARTS with ``text`` (the reference's entity
    criterion ``"<Account>*"``, so ``<Account> (subsidiary)`` counts): wildcards inside the text
    are escaped, quotes doubled."""
    safe = text.replace("~", "~~").replace("*", "~*").replace("?", "~?").replace('"', '""')
    return f'"{safe}*"'


def sheet_ref(name: str) -> str:
    """A sheet name as a formula writes it before ``!``: unquoted when it is a plain word (the
    reference writes ``Jobs!$C$2``), quoted with doubled apostrophes otherwise."""
    if _PLAIN_SHEET_NAME.match(name) and not _CELL_LIKE.match(name):
        return name
    return "'" + name.replace("'", "''") + "'"


def font(spec: FontSpec, *, bold: bool | None = None) -> Font:
    """openpyxl font from a theme font (``bold`` overrides the weight: total rows)."""
    return Font(
        name=spec.name,
        size=spec.size_pt,
        bold=spec.bold if bold is None else bold,
        italic=spec.italic,
        underline="single" if spec.underline else None,
        color=spec.color,
    )


def solid_fill(color: str) -> PatternFill:
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


@dataclass(frozen=True)
class Look:
    """What one cell looks like: the parts its named style is built from. ``wrap`` and ``align``
    are a column's overrides of its kind (None: the kind decides); ``fill`` names a theme fill;
    ``border`` overrides the header's default; ``font_role`` picks the paragraph font."""

    role: Role
    kind: ColumnKind = "label"
    wrap: bool | None = None
    align: str | None = None
    bold: bool = False
    fill: str | None = None
    border: bool | None = None
    font_role: Literal["body", "note"] = "body"

    @property
    def name(self) -> str:
        """The named style's name, derived from the parts (stable across workbooks)."""
        if self.role == "header":
            return "Export header" + (" bordered" if self.border else "")
        if self.role == "title":
            return "Export title"
        if self.role == "note":
            return "Export note"
        if self.role == "paragraph":
            return f"Export paragraph {self.font_role}"
        if self.role == "link":
            base = "Export link"
        elif self.fill:
            base = f"Export fill {self.fill} {self.kind}"
        else:
            base = f"Export body {self.kind}"
        parts = [base]
        if self.wrap is not None:
            parts.append("wrap" if self.wrap else "nowrap")
        if self.align:
            parts.append(self.align)
        if self.bold:
            parts.append("bold")
        if self.role == "link" and self.fill:
            parts.append(f"fill {self.fill}")
        return " ".join(parts)


def _column_look(col: Column, *, bold: bool = False, fill: str | None = None) -> Look:
    return Look("body", col.kind, wrap=col.wrap, align=col.align, bold=bold, fill=fill)


def _link_look(col: Column | None, *, bold: bool = False, fill: str | None = None) -> Look:
    return Look(
        "link",
        "url",
        wrap=col.wrap if col else None,
        align=col.align if col else None,
        bold=bold,
        fill=fill,
    )


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
        self._looks: dict[str, Look] = {}
        self._row_cache: dict[Any, tuple[tuple[str, ...], tuple[str | None, ...]]] = {}
        # (sheet title, column index) -> the width set by ``header`` (see there)
        self._widths: dict[tuple[str, int], float] = {}
        # every look registered up front in a fixed order, so style indices (and the stylesheet)
        # are the same for every workbook whatever its content (the same look)
        self._register(Look("header"))
        self._register(Look("header", border=True))
        self._register(Look("title"))
        self._register(Look("note"))
        self._register(Look("paragraph", font_role="body"))
        self._register(Look("paragraph", font_role="note"))
        for kind in self.theme.column_kinds:
            self._register_variants(Look("body", kind))
        self._register_variants(_link_look(None))
        self.limit = cfg.rules.limits.excel_cell_chars

    # ---- sheets and tables ------------------------------------------------------------------

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

    def header(
        self,
        ws: Worksheet,
        columns: Sequence[Column],
        row: int = 1,
        freeze: bool = True,
        *,
        freeze_columns: int = 0,
        height_pt: float | None = None,
        border: bool | None = None,
    ) -> None:
        """Header row in the header look (bold on the header fill, vertical center, wrapped; a
        border only when ``border`` or the theme asks), column widths (the column's own width,
        else its kind's; never narrowed when a table above set a wider one), the row height
        (``height_pt``, else the theme's ``header_height_pt``, else Excel's), and the freeze
        pane below the header and right of ``freeze_columns`` (for the sheet's main table)."""
        style = self._register(Look("header", border=border))
        kinds = self.theme.column_kinds
        for j, col in enumerate(columns, start=1):
            cell = ws.cell(row=row, column=j)
            text_cell(cell, col.header)
            cell.style = style
            width = col.width if col.width is not None else kinds[col.kind].width
            # widths are tracked here: asking openpyxl for an unset dimension creates one at its
            # own default width, which would then be written and would hide a narrower theme width
            key = (ws.title, j)
            if self._widths.get(key, 0.0) < width:
                self._widths[key] = width
                ws.column_dimensions[get_column_letter(j)].width = width
            self._register_column(col)
        height = height_pt if height_pt is not None else self.theme.header_height_pt
        if height is not None:
            ws.row_dimensions[row].height = height
        if freeze and self.theme.freeze_header:
            ws.freeze_panes = f"{get_column_letter(freeze_columns + 1)}{row + 1}"

    def set_width(self, ws: Worksheet, column: int, width: float) -> None:
        """A column width set outside ``header`` (a Summary sheet's first column); the wider of
        this and any width already set wins."""
        key = (ws.title, column)
        if self._widths.get(key, 0.0) < width:
            self._widths[key] = width
            ws.column_dimensions[get_column_letter(column)].width = width

    def finish_table(
        self, ws: Worksheet, columns: Sequence[Column], n_rows: int, header_row: int = 1
    ) -> None:
        """Filter over the header and data rows (when configured)."""
        if self.theme.autofilter:
            last = get_column_letter(len(columns))
            ws.auto_filter.ref = f"A{header_row}:{last}{header_row + max(0, n_rows)}"

    def body(
        self,
        ws: Worksheet,
        columns: Sequence[Column],
        values: dict[str, Any],
        row: int,
        ref: str,
        *,
        bold: bool = False,
        fill: str | None = None,
        link_text: str | None = None,
    ) -> None:
        """One data row: each value styled by its column (kind, wrap, alignment), bold for a
        total row, every cell on the theme fill ``fill`` when given (a row new since the previous
        research). A ``url`` column whose ``link_text`` is set (or ``link_text`` here as the
        default label) writes a link cell: the label in the link font, the address as the
        hyperlink. Long text continues on the Long text sheet (counted)."""
        names, links = self._row_styles(tuple(columns), bold, fill)
        for j, col in enumerate(columns, start=1):
            cell = ws.cell(row=row, column=j)
            value = values.get(col.key)
            label = col.link_text or (link_text if col.kind == "url" else None)
            link_style = links[j - 1]
            if label and value and link_style:
                text_cell(cell, label)
                cell.hyperlink = str(value)
                cell.style = link_style
                continue
            if value is None:
                text_cell(cell, "")
            elif isinstance(value, bool):
                labels = self.cfg.rules.labels
                text_cell(cell, labels.yes if value else labels.no)
            elif isinstance(value, date | int | float):
                cell.value = value
            else:
                text_cell(cell, self._fit(str(value), ref, col.header))
            cell.style = names[j - 1]

    def link(
        self,
        ws: Worksheet,
        row: int,
        column: int,
        text: str,
        url: str,
        *,
        col: Column | None = None,
        bold: bool = False,
        fill: str | None = None,
    ) -> None:
        """A hyperlink cell: ``text`` shown in the link font (blue, underlined), ``url`` as the
        hyperlink target; a string cell, never a formula (B11). ``col`` carries the column's wrap
        and alignment overrides when the cell sits in a configured column."""
        cell = ws.cell(row=row, column=column)
        text_cell(cell, text)
        cell.hyperlink = url
        cell.style = self._register(_link_look(col, bold=bold, fill=fill))

    def fill(self, ws: Worksheet, row: int, column: int, name: str) -> None:
        """Apply the theme fill ``name`` (``excel.fills``) to a cell this book has written,
        keeping its kind, wrapping, alignment, weight and link font. A cell not yet styled gets
        the fill on the ``label`` body look."""
        if name not in self.theme.fills:
            raise ValueError(f"fill {name!r} is not a theme fill")
        cell = ws.cell(row=row, column=column)
        look = self._looks.get(str(cell.style))
        if look is None or look.role not in ("body", "link"):
            look = Look("body", "label")
        cell.style = self._register(replace(look, fill=name))

    def fill_row(self, ws: Worksheet, row: int, columns: Sequence[Column], name: str) -> None:
        """The theme fill ``name`` on every cell of a written row (the new-row highlight)."""
        for j in range(1, len(columns) + 1):
            self.fill(ws, row, j, name)

    def style(
        self,
        ws: Worksheet,
        row: int,
        column: int,
        kind: ColumnKind,
        *,
        bold: bool = False,
        fill: str | None = None,
        wrap: bool | None = None,
        align: str | None = None,
    ) -> None:
        """The body look of ``kind`` on one cell written outside ``body`` (a base count, a
        Counts value): bordered, the kind's number format, bold or filled when asked."""
        cell = ws.cell(row=row, column=column)
        cell.style = self._register(
            Look("body", kind, wrap=wrap, align=align, bold=bold, fill=fill)
        )

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

    # ---- lines outside tables -----------------------------------------------------------------

    def note(self, ws: Worksheet, row: int, text: str, column: int = 1) -> None:
        """A visible note line in the note font (for example "None found")."""
        cell = ws.cell(row=row, column=column)
        text_cell(cell, text)
        cell.style = self._register(Look("note"))

    def title_line(
        self, ws: Worksheet, row: int, text: str, *, height_pt: float | None = None
    ) -> None:
        """A sheet's title line in the theme's title font, its row at the theme's title height
        (``height_pt`` overrides; None in both leaves Excel's)."""
        cell = ws.cell(row=row, column=1)
        text_cell(cell, text)
        cell.style = self._register(Look("title"))
        height = height_pt if height_pt is not None else self.theme.title_height_pt
        if height is not None:
            ws.row_dimensions[row].height = height

    def paragraph(
        self,
        ws: Worksheet,
        row: int,
        text: str,
        span: int,
        *,
        font_role: Literal["body", "note"] = "body",
        height_pt: float | None = None,
    ) -> None:
        """An explanatory paragraph in the body font (or the note font), wrapped across the first
        ``span`` columns (merged), its row ``height_pt`` tall, else tall enough for the wrapped
        lines estimated from the actual column widths (Excel does not size a merged row itself:
        call after the sheet's column widths are set)."""
        cell = ws.cell(row=row, column=1)
        text_cell(cell, text)
        cell.style = self._register(Look("paragraph", font_role=font_role))
        if span > 1:
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
        if height_pt is None:
            height_pt = self.estimate_height(ws, text, span, font_role)
        ws.row_dimensions[row].height = height_pt

    def estimate_height(
        self, ws: Worksheet, text: str, span: int, font_role: Literal["body", "note"] = "body"
    ) -> float:
        """Row height in points for ``text`` wrapped across the first ``span`` columns at their
        current widths (a column without a set width counts at the sheet's default width)."""
        default = ws.sheet_format.defaultColWidth or ws.sheet_format.baseColWidth or 1
        widths = [self._widths.get((ws.title, j)) or default for j in range(1, span + 1)]
        per_line = max(1.0, sum(widths))
        lines = max(1, -(-int(len(text) * WRAP_SLACK) // int(per_line)))
        spec = self.theme.fonts.note if font_role == "note" else self.theme.fonts.body
        return lines * spec.size_pt * LINE_SPACING

    def formula(self, f: Formula, *, bold: bool = False, fill: str | None = None) -> None:
        """Write one planned formula cell, styled by its column kind (the only way a formula
        enters a workbook: every text cell is a string cell, B11); bold for a total row."""
        cell = self.wb[f.sheet].cell(row=f.row, column=f.column)
        cell.value = f.text
        cell.style = self._register(Look("body", f.kind, bold=bold, fill=fill))

    # ---- named styles -------------------------------------------------------------------------
    # One named style per look, registered once per workbook and assigned by name (per-cell style
    # objects are re-hashed by openpyxl on every assignment: the hot path). Registration also adds
    # the look's cell format to the workbook's format table, so the stylesheet lists every
    # registered look whether or not a cell uses it: content never changes the stylesheet.

    def _register(self, look: Look) -> str:
        name = look.name
        if name in self._styles:
            return name
        style = self._build(look)
        self.wb.add_named_style(style)
        self.wb._cell_styles.add(copy(style.as_tuple()))  # type: ignore[attr-defined]
        self._styles.add(name)
        self._looks[name] = look
        return name

    def _register_variants(self, base: Look) -> None:
        """The plain, bold, filled and bold-filled variants of a body or link look (every
        combination a renderer can ask for, so none is first registered by a data row)."""
        for bold in (False, True):
            self._register(replace(base, bold=bold))
            for name in self.theme.fills:
                self._register(replace(base, bold=bold, fill=name))

    def _register_column(self, col: Column) -> None:
        """The looks of a column with overrides (its kind's looks are registered up front)."""
        if col.wrap is None and col.align is None:
            return
        self._register_variants(_column_look(col))
        if col.kind == "url":
            self._register_variants(_link_look(col))

    def _row_styles(
        self, columns: tuple[Column, ...], bold: bool, fill: str | None
    ) -> tuple[tuple[str, ...], tuple[str | None, ...]]:
        """Per column of a row: the body style name and, for a url column, the link style name
        (cached per layout: ``body`` is the hot path)."""
        key: Any = (columns, bold, fill)
        try:
            cached = self._row_cache.get(key)
        except TypeError:  # an unhashable column sequence: compute every time
            cached, key = None, None
        if cached is not None:
            return cached
        names = tuple(self._register(_column_look(c, bold=bold, fill=fill)) for c in columns)
        links = tuple(
            self._register(_link_look(c, bold=bold, fill=fill)) if c.kind == "url" else None
            for c in columns
        )
        if key is not None:
            self._row_cache[key] = (names, links)
        return names, links

    def _build(self, look: Look) -> NamedStyle:
        t = self.theme
        kind: ColumnStyle = t.column_kinds[look.kind]
        label: ColumnStyle = t.column_kinds["label"]
        wrap = kind.wrap if look.wrap is None else look.wrap
        if look.role == "header":
            bordered = t.header_border if look.border is None else look.border
            return self._style(
                look.name,
                font(t.fonts.header),
                solid_fill(t.header_fill),
                border(t) if bordered else None,
                label.number_format,
                Alignment(wrap_text=True, vertical=t.header_vertical),
            )
        if look.role == "title":
            return self._style(
                look.name,
                font(t.fonts.title),
                None,
                None,
                label.number_format,
                Alignment(wrap_text=False, vertical=t.body_vertical),
            )
        if look.role == "note":
            return self._style(
                look.name,
                font(t.fonts.note),
                None,
                None,
                label.number_format,
                Alignment(wrap_text=label.wrap, vertical=t.body_vertical),
            )
        if look.role == "paragraph":
            return self._style(
                look.name,
                font(getattr(t.fonts, look.font_role)),
                None,
                None,
                t.column_kinds["text_long"].number_format,
                Alignment(wrap_text=True, vertical=t.body_vertical),
            )
        spec = t.fonts.link if look.role == "link" else t.fonts.body
        return self._style(
            look.name,
            font(spec, bold=True if look.bold else None),
            solid_fill(t.fills[look.fill]) if look.fill else None,
            border(t),
            kind.number_format,
            Alignment(wrap_text=wrap, vertical=t.body_vertical, horizontal=look.align),
        )

    @staticmethod
    def _style(
        name: str,
        f: Font,
        filled: PatternFill | None,
        b: Border | None,
        number_format: str,
        alignment: Alignment,
    ) -> NamedStyle:
        style = NamedStyle(name=name, font=f, number_format=number_format, alignment=alignment)
        if filled is not None:
            style.fill = filled
        if b is not None:
            style.border = b
        return style

    # Legacy entry points kept for the renderers written against the first Book: a renderer
    # names a style and supplies its parts. New code asks for a look (``style``, ``fill``,
    # ``link``, ``body(bold=..., fill=...)``) and never builds a style itself.

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
            style = self._style(
                name,
                f,
                filled,
                b,
                kind.number_format,
                Alignment(
                    wrap_text=kind.wrap if wrap is None else wrap, vertical=self.theme.body_vertical
                ),
            )
            self.wb.add_named_style(style)
            self.wb._cell_styles.add(copy(style.as_tuple()))  # type: ignore[attr-defined]
            self._styles.add(name)
        return name

    def _note_style(self) -> str:
        return self._register(Look("note"))

    def _header_style(self) -> str:
        return self._register(Look("header"))

    def _body_style(self, kind: ColumnKind) -> str:
        return self._register(Look("body", kind))

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
    count_col: str = "A",
) -> None:
    """Counts sheet: label, value, base. Lines marked ``rows``/``open``/``closed`` are formulas
    over the data sheet (B12); the verification recalculates them against the code counts.
    ``count_col`` is the data sheet column filled on every row (the Ref column)."""
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
            span = f"'{data_sheet}'!{count_col}2:{count_col}{max(2, last)}"
            cell.value = f"=COUNTA({span})"
        elif line.formula in ("open", "closed") and status_col:
            word = status_words[line.formula].replace('"', '""')
            span = f"'{data_sheet}'!{status_col}2:{status_col}{max(2, last)}"
            cell.value = f'=COUNTIF({span},"{word}")'
    book.finish_table(ws, cols, len(lines))
