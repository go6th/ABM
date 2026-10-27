#!/usr/bin/env python3
"""Parity checker: compare generated deliverables against a golden file set (any account).

Checks every .docx and .xlsx that exists in both folders (matched by file name) for
structure, values, formulas and styling, and reports every difference.

Usage
    python3 parity_check.py GOLDEN_DIR CANDIDATE_DIR [--values-only] [--max 15]

    GOLDEN_DIR     folder with the reference files (a finished workspace's deliverables/)
    CANDIDATE_DIR  folder with the product's output, or the files downloaded from the UI
    --values-only  skip style checks (fonts, fills, widths); keep structure and values
    --max N        print at most N differences per file (default 15)

Exit code 0 when every file matches, 1 otherwise.

xlsx: sheet names and order, dimensions, freeze panes, autofilter, merged ranges,
      column widths, conditional formatting ranges, every cell value (formulas compared
      as formula text), hyperlink targets, number formats, header row styling, and
      whether cached formula values exist (a file with no cached values shows blanks in
      any reader that does not recalculate).
docx: page size, orientation, margins, footer PAGE field, block sequence (paragraph style
      and text, table shape and every cell's text), first run font size, bold and color,
      table header fill, bullet or numbered list type.
"""
import argparse, os, re, sys, zipfile

import docx
import openpyxl
from docx.table import Table
from docx.text.paragraph import Paragraph

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class Report:
    def __init__(self, name, cap):
        self.name, self.cap, self.items = name, cap, []

    def add(self, where, gold, cand):
        self.items.append((where, gold, cand))

    def show(self):
        status = "PASS" if not self.items else f"FAIL ({len(self.items)} differences)"
        print(f"\n== {self.name}: {status}")
        for where, g, c in self.items[: self.cap]:
            print(f"   {where}\n      golden:    {str(g)[:160]!r}\n      candidate: {str(c)[:160]!r}")
        if len(self.items) > self.cap:
            print(f"   ... {len(self.items) - self.cap} more")
        return not self.items


# ---------------------------------------------------------------- xlsx
def font_sig(c):
    f = c.font
    col = f.color.rgb if f is not None and f.color is not None and f.color.type == "rgb" else None
    return (bool(f.b), f.sz, col)


def fill_sig(c):
    return c.fill.fgColor.rgb if c.fill is not None and c.fill.fill_type else None


def widths(ws):
    out = {}
    for k, d in ws.column_dimensions.items():
        if d.width:
            for i in range(d.min or 0, (d.max or 0) + 1):
                if i:
                    out[openpyxl.utils.get_column_letter(i)] = round(d.width, 1)
    return out


def cmp_xlsx(g_path, c_path, rep, values_only):
    g, c = openpyxl.load_workbook(g_path), openpyxl.load_workbook(c_path)
    if g.sheetnames != c.sheetnames:
        rep.add("sheet names/order", g.sheetnames, c.sheetnames)
    gv = openpyxl.load_workbook(c_path, data_only=True)
    for name in g.sheetnames:
        if name not in c.sheetnames:
            continue
        x, y = g[name], c[name]
        for attr in ("max_row", "max_column", "freeze_panes"):
            if getattr(x, attr) != getattr(y, attr):
                rep.add(f"{name}: {attr}", getattr(x, attr), getattr(y, attr))
        if x.auto_filter.ref != y.auto_filter.ref:
            rep.add(f"{name}: autofilter", x.auto_filter.ref, y.auto_filter.ref)
        mg, mc = sorted(map(str, x.merged_cells.ranges)), sorted(map(str, y.merged_cells.ranges))
        if mg != mc:
            rep.add(f"{name}: merged ranges", mg, mc)
        cg = sorted(str(r.sqref) for r in x.conditional_formatting)
        cc = sorted(str(r.sqref) for r in y.conditional_formatting)
        if cg != cc:
            rep.add(f"{name}: conditional formatting", cg, cc)
        if not values_only and widths(x) != widths(y):
            rep.add(f"{name}: column widths", widths(x), widths(y))
        formulas = 0
        for r in range(1, max(x.max_row, y.max_row) + 1):
            for col in range(1, max(x.max_column, y.max_column) + 1):
                a, b = x.cell(r, col), y.cell(r, col)
                if a.value != b.value:
                    rep.add(f"{name}!{a.coordinate} value", a.value, b.value)
                if isinstance(a.value, str) and a.value.startswith("="):
                    formulas += 1
                ha = a.hyperlink.target if a.hyperlink else None
                hb = b.hyperlink.target if b.hyperlink else None
                if ha != hb:
                    rep.add(f"{name}!{a.coordinate} hyperlink", ha, hb)
                if a.value is not None and a.number_format != b.number_format:
                    rep.add(f"{name}!{a.coordinate} number format", a.number_format, b.number_format)
                if not values_only and a.value is not None and (font_sig(a), fill_sig(a)) != (font_sig(b), fill_sig(b)):
                    rep.add(f"{name}!{a.coordinate} style (bold,size,color / fill)",
                            (font_sig(a), fill_sig(a)), (font_sig(b), fill_sig(b)))
        if formulas:
            ys = gv[name]
            missing = sum(1 for row in y.iter_rows() for cl in row
                          if isinstance(cl.value, str) and cl.value.startswith("=")
                          and ys[cl.coordinate].value is None)
            if missing:
                rep.add(f"{name}: formulas without cached values (not recalculated)", 0, missing)


# ---------------------------------------------------------------- docx
def blocks(path):
    d = docx.Document(path)
    out = []
    for el in d.element.body.iterchildren():
        tag = el.tag.split("}")[1]
        if tag == "p":
            p = Paragraph(el, d)
            if not p.text.strip():
                continue
            r = p.runs[0] if p.runs else None
            sz = r.font.size.pt if r is not None and r.font.size else None
            colr = str(r.font.color.rgb) if r is not None and r.font.color is not None and r.font.color.type else None
            numpr = el.find(f"{W}pPr/{W}numPr")
            lst = None
            if numpr is not None:
                nid = numpr.find(f"{W}numId").get(f"{W}val")
                lst = f"list:{nid}"
            out.append(("P", p.style.name, p.text, sz, bool(r.bold) if r else None, colr, lst))
        elif tag == "tbl":
            t = Table(el, d)
            cells = [[c.text for c in row.cells] for row in t.rows]
            sh = el.find(f".//{W}tr/{W}tc/{W}tcPr/{W}shd")
            fill = sh.get(f"{W}fill") if sh is not None else None
            out.append(("T", len(t.rows), len(t.columns), cells, fill))
    return d, out


def footer_has_page_field(path):
    z = zipfile.ZipFile(path)
    xml = "".join(z.read(n).decode("utf8", "ignore") for n in z.namelist() if n.startswith("word/footer"))
    return bool(re.search(r"PAGE", re.sub(r"<w:t[^>]*>[^<]*</w:t>", "", xml)))


def cmp_docx(g_path, c_path, rep, values_only):
    gd, gb = blocks(g_path)
    cd, cb = blocks(c_path)
    gs, cs = gd.sections[0], cd.sections[0]
    for attr in ("page_width", "page_height", "orientation", "left_margin", "right_margin", "top_margin", "bottom_margin"):
        if getattr(gs, attr) != getattr(cs, attr):
            rep.add(f"section {attr}", getattr(gs, attr), getattr(cs, attr))
    if footer_has_page_field(g_path) and not footer_has_page_field(c_path):
        rep.add("footer page number", "live PAGE field", "static text (field lost, file was re-saved or converted)")
    gT = [b for b in gb if b[0] == "T"]
    cT = [b for b in cb if b[0] == "T"]
    if len(gT) != len(cT):
        rep.add("table count", len(gT), len(cT))
    gH = [b[2] for b in gb if b[0] == "P" and b[1].startswith("Heading")]
    cH = [b[2] for b in cb if b[0] == "P" and b[1].startswith("Heading")]
    if gH != cH:
        missing = [h for h in gH if h not in cH]
        extra = [h for h in cH if h not in gH]
        rep.add("heading sequence", f"missing {missing[:5]}", f"extra {extra[:5]}")
    for i, (a, b) in enumerate(zip(gb, cb)):
        if a[0] != b[0]:
            rep.add(f"block {i} type", a[:3], b[:3])
            break
        if a[0] == "P":
            if a[2] != b[2]:
                rep.add(f"block {i} paragraph text", a[2], b[2])
            if a[1] != b[1]:
                rep.add(f"block {i} paragraph style", a[1], b[1])
            if not values_only and a[3:6] != b[3:6]:
                rep.add(f"block {i} first run (size, bold, color)", a[3:6], b[3:6])
            if (a[6] is None) != (b[6] is None):
                rep.add(f"block {i} list paragraph", a[6], b[6])
        else:
            if a[1:3] != b[1:3]:
                rep.add(f"block {i} table shape (rows, cols)", a[1:3], b[1:3])
            if a[3][:1] != b[3][:1]:
                rep.add(f"block {i} table header", a[3][:1], b[3][:1])
            for r, (ra, rb) in enumerate(zip(a[3], b[3])):
                if ra != rb:
                    rep.add(f"block {i} table row {r}", ra, rb)
            if not values_only and a[4] != b[4]:
                rep.add(f"block {i} header fill", a[4], b[4])
    if len(gb) != len(cb):
        rep.add("block count (paragraphs + tables)", len(gb), len(cb))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("golden"); ap.add_argument("candidate")
    ap.add_argument("--values-only", action="store_true"); ap.add_argument("--max", type=int, default=15)
    a = ap.parse_args()
    ok = True
    names = sorted(f for f in os.listdir(a.golden) if f.endswith((".docx", ".xlsx")))
    for n in names:
        cp = os.path.join(a.candidate, n)
        rep = Report(n, a.max)
        if not os.path.exists(cp):
            rep.add("file", "present", "MISSING")
        elif n.endswith(".xlsx"):
            cmp_xlsx(os.path.join(a.golden, n), cp, rep, a.values_only)
        else:
            cmp_docx(os.path.join(a.golden, n), cp, rep, a.values_only)
        ok &= rep.show()
    print("\nOVERALL:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
