"""Build the audit PDF: python build.py  (run with the scratchpad venv: needs markdown, pypdf)."""
import re, subprocess, sys, pathlib
from html import escape

HERE = pathlib.Path(__file__).parent
OUT = HERE.parent
sys.path.insert(0, str(HERE))

import kit
import content_a, content_b, content_c, content_d, appendix

CSS = r"""
@page { size: A4; margin: 17mm 15mm 18mm 15mm;
  @bottom-left { content: "Account Intelligence (ABM Generator): project audit"; font: 8pt Helvetica, Arial, sans-serif; color: #7b8494; }
  @bottom-right { content: counter(page); font: 8pt Helvetica, Arial, sans-serif; color: #7b8494; } }
@page :first { @bottom-left { content: none; } @bottom-right { content: none; } }
:root { --ink:#1b2430; --mute:#5c6778; --line:#d8dee8; --blue:#2f5fb3; --soft:#f3f6fb; }
* { box-sizing: border-box; }
html { font-family: "Helvetica Neue", Helvetica, Arial, "DejaVu Sans", sans-serif; color: var(--ink); font-size: 9.6pt; line-height: 1.42; }
body { margin: 0; }
h1,h2,h3,h4 { font-weight: 700; line-height: 1.2; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font-family: "DejaVu Sans Mono", monospace; font-size: 6.6pt; background: #f3f6fb; padding: 2mm; border-radius: 4px; margin: 2mm 0 4mm; }
pre code { background: none; padding: 0; font-size: inherit; }
td, th, li, p, code, h2, h3 { overflow-wrap: anywhere; }
.cover { height: 258mm; width: 100%; padding: 30mm 16mm; border-radius: 10px; background: linear-gradient(160deg,#16294f 0%,#27498f 60%,#3b6fc4 100%); color: #fff; page-break-after: always; position: relative; }
.cover .kicker { letter-spacing: 2px; font-size: 10pt; text-transform: uppercase; opacity: .8; }
.cover h1 { font-size: 36pt; margin: 14mm 0 6mm; line-height: 1.08; }
.cover p { font-size: 13pt; max-width: 150mm; opacity: .92; }
.cover .meta { position: absolute; left: 16mm; bottom: 16mm; width: 140mm; font-size: 10pt; opacity: .85; line-height: 1.6; }
.toc { page-break-after: always; }
.toc h2 { font-size: 20pt; margin: 0 0 6mm; }
.toc ul { list-style: none; padding: 0; margin: 0; }
.toc li { display: flex; align-items: baseline; padding: 1.2mm 0; border-bottom: 1px dotted var(--line); }
.toc li.tpart { margin-top: 3.5mm; font-weight: 700; border-bottom: 1px solid var(--blue); color: var(--blue); }
.toc .tt { flex: 1; }
.toc .pg { width: 12mm; text-align: right; color: var(--mute); }
.part { page-break-before: always; page-break-after: always; height: 255mm; display: flex; align-items: center; }
.part-inner { border-left: 6px solid var(--blue); padding-left: 10mm; }
.part-inner h1 { font-size: 28pt; margin: 0 0 4mm; color: var(--blue); }
.part-inner p { font-size: 13pt; color: var(--mute); max-width: 140mm; }
.chapter { page-break-before: always; }
.landscape { }
.ch-title { font-size: 19pt; margin: 0 0 3mm; padding-bottom: 2mm; border-bottom: 2px solid var(--blue); }
.ch-num { color: var(--blue); margin-right: 3mm; }
.lead { font-size: 11.2pt; color: var(--mute); margin: 0 0 5mm; }
h3 { font-size: 12pt; margin: 6mm 0 2mm; color: #213a6b; break-after: avoid; }
h4 { font-size: 10.5pt; margin: 5mm 0 2mm; color: #213a6b; break-after: avoid; }
h5, h6 { font-size: 9.6pt; margin: 3mm 0 1mm; break-after: avoid; }
p { margin: 0 0 2.6mm; orphans: 3; widows: 3; }
ul, ol { margin: 0 0 3mm; padding-left: 5.5mm; }
li { margin-bottom: 1.2mm; }
th code { background: rgba(255,255,255,.22); color: #fff; }
code { font-family: "DejaVu Sans Mono", Menlo, monospace; font-size: 8.3pt; background: #eef2f8; padding: 0 1.2mm; border-radius: 2px; word-break: break-word; }
table.t { width: 100%; border-collapse: collapse; margin: 2mm 0 4mm; font-size: 8.6pt; }
table.t th { background: #213a6b; color: #fff; text-align: left; padding: 1.6mm 2mm; font-weight: 600; }
table.t td { padding: 1.5mm 2mm; border-bottom: 1px solid var(--line); vertical-align: top; }
table.t tr:nth-child(even) td { background: var(--soft); }
table.t tr { break-inside: avoid; }
table.t thead { display: table-header-group; }
table.sm { font-size: 7.8pt; }
.md table.t { font-size: 6.9pt; table-layout: fixed; }
.md table.t td, .md table.t th { padding: 1mm 1.4mm; word-break: break-word; }
.md p { font-size: 8.4pt; } .md li { font-size: 8.4pt; }
figure.fig { margin: 3mm 0 5mm; break-inside: avoid; border: 1px solid var(--line); border-radius: 6px; padding: 3mm; background: #fff; }
figure.fig svg { display: block; width: 100%; height: auto; }
figcaption { font-size: 8.4pt; color: var(--mute); margin-top: 2mm; }
.box { border-radius: 6px; padding: 3mm 4mm; margin: 3mm 0 4mm; break-inside: avoid; border-left: 5px solid; }
.box-title { font-weight: 700; margin-bottom: 1.5mm; }
.box p, .box ul, .box ol { margin-bottom: 1.5mm; font-size: 9.2pt; }
.box.good { background: #ecfaf1; border-color: #3f9d63; } .box.good .box-title { color: #1b6b3d; }
.box.risk { background: #fdeeee; border-color: #d65a5a; } .box.risk .box-title { color: #a02727; }
.box.todo { background: #eef4ff; border-color: #4a7fd4; } .box.todo .box-title { color: #1f4aa0; }
.box.warn { background: #fff7e6; border-color: #d99a2b; } .box.warn .box-title { color: #8a5a07; }
.box.note { background: #f3f6fb; border-color: #8a9ab8; } .box.note .box-title { color: #34476e; }
.sev { display: inline-block; white-space: nowrap; font-size: 7.4pt; font-weight: 700; padding: .4mm 2mm; border-radius: 9px; color: #fff; }
.sev.high { background: #c93c3c; } .sev.medium { background: #d98a1f; } .sev.low { background: #5a86c8; } .sev.info { background: #7b8494; }
"""


def toc_html(pages):
    items = []
    for kind, anchor, title in ENTRIES:
        pg = pages.get(anchor, "")
        cls = "tpart" if kind == "part" else ""
        items.append(f'<li class="{cls}"><span class="tt">{escape(title)}</span><span class="pg">{pg}</span></li>')
    return '<section class="toc"><h2>Contents</h2><ul>' + "".join(items) + "</ul></section>"


def cover():
    return ('<section class="cover"><div class="kicker">Project audit and handover guide</div>'
            "<h1>Account Intelligence<br>(ABM Generator)</h1>"
            "<p>How the website works from zero to one hundred: the product, the code, every route, every prompt, "
            "the data, the cloud, the risks. Written for someone who inherited it and did not build it.</p>"
            '<div class="meta">Audit date: 3 November 2026<br>Repository: ABM Generator, branch feat/final-product, commit d3831549<br>'
            "Method: five parallel read-only code audits plus review of the existing guide. Nothing was run, deployed or paid for.</div></section>")


def assemble(pages):
    kit.CHAPTERS.clear(); kit._FIGS["n"] = 0
    body = "\n".join([content_a.build(), content_b.build(), content_c.build(), content_d.build(), appendix.build()])
    return body


# Entries for the TOC: built from the body after the first assembly.
ENTRIES = []


def collect_entries(body):
    ENTRIES.clear()
    for m in re.finditer(r'<section class="part"><div class="part-inner"><h1>(.*?)</h1>|<section class="chapter" id="(ch\d+)"><h2 class="ch-title" data-ch="ch\d+"><span class="ch-num">(.*?)</span> (.*?)</h2>', body):
        if m.group(1):
            ENTRIES.append(("part", "p:" + re.sub(r"\s+", " ", m.group(1)), re.sub(r"\s+", " ", m.group(1))))
        else:
            ENTRIES.append(("ch", m.group(2), f"{m.group(3)}  {re.sub('<.*?>', '', m.group(4))}"))


def write_html(body, pages, path):
    html = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Account Intelligence: project audit</title>'
            f"<style>{CSS}</style></head><body>{cover()}{toc_html(pages)}{body}</body></html>")
    path.write_text(html, encoding="utf-8")


def render(html_path, pdf_path):
    subprocess.run(["chromium", "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}", f"file://{html_path}"], capture_output=True, timeout=300, check=True)


def page_map(pdf_path):
    from pypdf import PdfReader
    r = PdfReader(str(pdf_path))
    texts = [re.sub(r"\s+", " ", (pg.extract_text() or "")) for pg in r.pages]
    # skip cover and the contents pages: start after the first page that begins a Part
    start = next((i for i, t in enumerate(texts) if i > 1 and "Part 1 The big picture" in t[:160] and "Contents" not in t[:160]), 2)
    pages = {}
    cursor = start
    for kind, anchor, title in ENTRIES:
        key = re.sub(r"\s+", " ", title.split("  ", 1)[1] if kind == "ch" else title).strip()
        key = key[:45]
        for i in range(cursor, len(texts)):
            if key in texts[i]:
                pages[anchor] = i + 1
                cursor = i
                break
    return pages, len(texts)


def main():
    body = assemble({})
    collect_entries(body)
    html_path = OUT / "ABM_Generator_Project_Audit.html"
    pdf_path = OUT / "ABM_Generator_Project_Audit.pdf"
    write_html(body, {}, html_path)
    render(html_path, pdf_path)
    pages, n = page_map(pdf_path)
    write_html(body, pages, html_path)
    render(html_path, pdf_path)
    pages2, n2 = page_map(pdf_path)
    print("pages", n, n2, "toc entries", len(ENTRIES), "mapped", len(pages2))
    missing = [t for k, a, t in ENTRIES if a not in pages2]
    print("missing:", missing[:10])


if __name__ == "__main__":
    main()
