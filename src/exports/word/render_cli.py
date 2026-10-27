"""Interim Word renderer behind the D-26 contract: ``DocumentSpec`` JSON on stdin, docx bytes on
stdout. Exit 0 on success, 2 when the input violates the contract, 3 when rendering fails.

This is NOT the legacy Node library (not supplied); it is a neutral python-docx renderer that runs
in the same sandbox (subprocess, cleared environment, no database credentials, bounded output) so
the whole pipeline is exercised for real. When the Node library is ported it replaces this
command; nothing else changes. Every style value comes from ``spec.theme``.
"""

from __future__ import annotations

import copy
import io
import sys
from functools import lru_cache
from typing import Any

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from docx.table import Table
from docx.text.paragraph import Paragraph
from pydantic import ValidationError

from exports.config import Font, WordTheme
from exports.word.contract import (
    BulletsBlock,
    DocumentSpec,
    HeadingBlock,
    ParagraphBlock,
    TableBlock,
    TitleBlock,
)

EXIT_BAD_INPUT = 2
EXIT_RENDER = 3


@lru_cache(maxsize=64)
def _rpr(spec: Font) -> Any:
    """The run properties of one theme font, built once (schema order: rFonts, b, i, color,
    sz, szCs) and copied into every run: building them per run is the renderer's hot path."""
    rpr = OxmlElement("w:rPr")
    fonts = OxmlElement("w:rFonts")
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), spec.name)
    rpr.append(fonts)
    for flag, on in (("w:b", spec.bold), ("w:i", spec.italic)):
        if on:
            rpr.append(OxmlElement(flag))
    color = OxmlElement("w:color")
    color.set(qn("w:val"), spec.color)
    rpr.append(color)
    half_points = str(round(spec.size_pt * 2))
    for tag in ("w:sz", "w:szCs"):
        size = OxmlElement(tag)
        size.set(qn("w:val"), half_points)
        rpr.append(size)
    return rpr


def _text(paragraph: Paragraph, text: str, spec: Font) -> None:
    """One run in the theme font; newlines in the text become line breaks."""
    _append_run(paragraph._p, text, spec)


def _append_run(p: Any, text: str, spec: Font) -> None:
    run = OxmlElement("w:r")
    run.append(copy.deepcopy(_rpr(spec)))
    for i, line in enumerate(text.split("\n")):
        if i:
            run.append(OxmlElement("w:br"))
        t = OxmlElement("w:t")
        t.text = line
        t.set(qn("xml:space"), "preserve")
        run.append(t)
    p.append(run)


def _fresh_numbering(doc: Any) -> int:
    """A new numbering instance of the List Number style's definition that starts at 1: every
    numbered list of the document counts from 1, not on from the previous list."""
    style_num = doc.styles["List Number"].element.pPr.numPr.numId.val
    numbering = doc.part.numbering_part.element
    abstract = next(n.abstractNumId.val for n in numbering.num_lst if n.numId == style_num)
    num = numbering.add_num(abstract)
    num.add_lvlOverride(ilvl=0).add_startOverride(1)
    return int(num.numId)


def _bullets(doc: Any, block: BulletsBlock, theme: WordTheme) -> None:
    """A bulleted or numbered list; a note goes under its item, indented like the item text."""
    sp = theme.spacing
    num_id = _fresh_numbering(doc) if block.numbered else None
    for i, item in enumerate(block.items):
        p = doc.add_paragraph(style="List Number" if block.numbered else "List Bullet")
        if num_id is not None:
            num_pr = p._p.get_or_add_pPr().get_or_add_numPr()
            num_pr.get_or_add_ilvl().val = 0
            num_pr.get_or_add_numId().val = num_id
        _text(p, item, theme.fonts.body)
        _spacing(p, 0, sp.paragraph_after_pt)
        note = block.notes[i] if block.notes else ""
        if note:
            q = doc.add_paragraph(style="List Continue")  # indented like the item text
            _text(q, note, theme.fonts.note)
            _spacing(q, 0, sp.paragraph_after_pt)


def _spacing(paragraph: Paragraph, before: float, after: float) -> None:
    fmt = paragraph.paragraph_format
    fmt.space_before = Pt(before)
    fmt.space_after = Pt(after)


def _border(tbl: Table, theme: WordTheme) -> None:
    tbl_pr = tbl._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(theme.table.border_size_eighth_pt))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), theme.table.border_color)
        borders.append(el)
    tbl_pr.append(borders)
    margins = OxmlElement("w:tblCellMar")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:w"), str(theme.table.cell_margin_twips))
        el.set(qn("w:type"), "dxa")
        margins.append(el)
    tbl_pr.append(margins)


def _row_props(row_el: Any, theme: WordTheme, header: bool) -> None:
    tr_pr = OxmlElement("w:trPr")
    row_el.append(tr_pr)
    if theme.table.row_cant_split:
        tr_pr.append(OxmlElement("w:cantSplit"))
    if header and theme.table.repeat_header_row:
        tr_pr.append(OxmlElement("w:tblHeader"))


def _row_template(theme: WordTheme, twips: list[int], header: bool) -> Any:
    """An empty table row: row properties, and per column a cell with its width (and the header
    fill on the header row) holding one paragraph with no spacing."""
    tr = OxmlElement("w:tr")
    _row_props(tr, theme, header)
    for width in twips:
        tc = OxmlElement("w:tc")
        tc_pr = OxmlElement("w:tcPr")
        tc_w = OxmlElement("w:tcW")
        tc_w.set(qn("w:w"), str(width))
        tc_w.set(qn("w:type"), "dxa")
        tc_pr.append(tc_w)
        if header:
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:color"), "auto")
            shd.set(qn("w:fill"), theme.table.header_fill)
            tc_pr.append(shd)
        tc.append(tc_pr)
        p = OxmlElement("w:p")
        p_pr = OxmlElement("w:pPr")
        spacing = OxmlElement("w:spacing")
        spacing.set(qn("w:before"), "0")
        spacing.set(qn("w:after"), "0")
        p_pr.append(spacing)
        p.append(p_pr)
        tc.append(p)
        tr.append(tc)
    return tr


def _table(doc: object, block: TableBlock, theme: WordTheme) -> None:
    page = theme.page
    text_width = page.width_in - page.margins_in.left - page.margins_in.right
    tbl = doc.add_table(rows=0, cols=len(block.headers))  # type: ignore[attr-defined]
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = False
    _border(tbl, theme)
    widths = [Inches(text_width * w / 100) for w in block.widths]
    for k, w in enumerate(widths):
        tbl.columns[k].width = w
    twips = [round(text_width * w / 100 * 1440) for w in block.widths]
    templates = (_row_template(theme, twips, header=True), _row_template(theme, twips, False))
    # rows are built from a template row (python-docx's add_row and cell accessors walk the
    # table and re-create properties per cell: the renderer's hot path on large tables)
    for r, values in enumerate((block.headers, *block.rows)):
        tr = copy.deepcopy(templates[0 if r == 0 else 1])
        spec = theme.fonts.table_header if r == 0 else theme.fonts.table_body
        for k, tc in enumerate(tr.findall(qn("w:tc"))):
            _append_run(tc.find(qn("w:p")), values[k], spec)
        tbl._tbl.append(tr)


def render(spec: DocumentSpec) -> bytes:
    """docx bytes for one spec (not yet normalised; the caller normalises the package)."""
    theme = spec.theme
    doc = Document()
    section = doc.sections[0]
    page = theme.page
    section.page_width, section.page_height = Inches(page.width_in), Inches(page.height_in)
    m = page.margins_in
    section.top_margin, section.bottom_margin = Inches(m.top), Inches(m.bottom)
    section.left_margin, section.right_margin = Inches(m.left), Inches(m.right)
    normal = doc.styles["Normal"]
    normal.font.name = theme.fonts.body.name
    normal.font.size = Pt(theme.fonts.body.size_pt)
    _text(section.header.paragraphs[0], spec.header_text, theme.fonts.header)
    _text(section.footer.paragraphs[0], spec.footer_text, theme.fonts.footer)
    sp = theme.spacing
    for block in spec.blocks:
        if isinstance(block, TitleBlock):
            p = doc.add_paragraph()
            _text(p, block.text, theme.fonts.title)
            _spacing(p, 0, sp.paragraph_after_pt)
            p = doc.add_paragraph()
            _text(p, block.subtitle, theme.fonts.subtitle)
            _spacing(p, 0, sp.heading_before_pt)
        elif isinstance(block, HeadingBlock):
            p = doc.add_paragraph()
            _text(p, block.text, theme.fonts.heading1 if block.level == 1 else theme.fonts.heading2)
            _spacing(p, sp.heading_before_pt, sp.heading_after_pt)
            p.paragraph_format.keep_with_next = True
        elif isinstance(block, ParagraphBlock):
            p = doc.add_paragraph()
            font = {"body": theme.fonts.body, "quote": theme.fonts.quote, "note": theme.fonts.note}[
                block.style
            ]
            _text(p, block.text, font)
            _spacing(p, 0, sp.paragraph_after_pt)
        elif isinstance(block, BulletsBlock):
            _bullets(doc, block, theme)
        else:
            _table(doc, block, theme)
            _spacing(doc.add_paragraph(), 0, sp.paragraph_after_pt)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def main() -> int:
    """Read the spec from stdin, write the docx to stdout."""
    try:
        spec = DocumentSpec.model_validate_json(sys.stdin.buffer.read())
    except (ValidationError, ValueError) as error:
        sys.stderr.write(f"contract violation: {error.__class__.__name__}\n")
        return EXIT_BAD_INPUT
    try:
        data = render(spec)
    except Exception as error:  # noqa: BLE001 - any renderer failure is one exit code
        sys.stderr.write(f"render failed: {error.__class__.__name__}\n")
        return EXIT_RENDER
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point, exercised through the subprocess
    sys.exit(main())
