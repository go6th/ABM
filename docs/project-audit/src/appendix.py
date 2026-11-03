"""Appendices: tables lifted from the audit reports and the old guide."""
import re
import markdown
from kit import *

S = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "reports")
REPO = "/home/sakib/ABM Generator"


def _section(path, start_pat, end_pat=None):
    lines = open(path).read().split("\n")
    s = next(i for i, l in enumerate(lines) if re.match(start_pat, l))
    e = len(lines)
    if end_pat:
        e = next((i for i in range(s + 1, len(lines)) if re.match(end_pat, lines[i])), len(lines))
    return "\n".join(lines[s:e])


def _md(text, demote=1):
    text = re.sub(r"^(#{1,6}) ", lambda m: "#" * min(6, len(m.group(1)) + demote) + " ", text, flags=re.M)
    html = markdown.markdown(text, extensions=["tables", "fenced_code"])
    html = html.replace("<table>", '<table class="t sm">')
    cg8 = "<colgroup>" + "".join(f'<col style="width:{w}%">' for w in (5, 18, 13, 13, 5, 24, 9, 13)) + "</colgroup>"
    html = re.sub(r'(<table class="t sm">)(\s*<thead>\s*<tr>\s*<th>Method</th>\s*<th>Path</th>)', lambda m: m.group(1) + cg8 + m.group(2), html)
    # headings from reports must not enter the TOC or page-break logic
    return html


def build():
    h = []
    h.append(part("Appendices", "The detailed inventories behind the chapters. They come straight from the five read-only audits and the previous guide, so they are dense. Use them as a reference."))

    h.append('<section class="landscape">')
    h.append(chapter("A", "Every API route (234)",
                     "Method, path, handler, who may call it, audit class, what it does, and who calls it. From the route audit; \"unverified\" is kept where the auditor could not confirm."))
    sec = _section(f"{S}/audit_routes.md", r"## 11\.", r"## 13\.")
    h.append('<div class="md">' + _md(sec, 1) + "</div>")
    h.append(end_chapter())
    h.append("</section>")

    h.append('<section class="landscape">')
    h.append(chapter("B", "Web pages and route handlers in detail",
                     "Every page with its components, backend calls and role needs."))
    sec = _section(f"{S}/audit_web.md", r"### 2\.3", r"---")
    h.append('<div class="md">' + _md(sec, 1) + "</div>")
    h.append(end_chapter())
    h.append("</section>")

    h.append(chapter("C", "Database tables by domain",
                     "About 205 tables, condensed to one line each, plus the relation summary used for the map in chapter 18."))
    sec = _section(f"{S}/audit_data_infra.md", r"### 2\.2", r"### 2\.4")
    h.append('<div class="md">' + _md(sec, 1) + "</div>")
    h.append(end_chapter())

    h.append(chapter("D", "Environment variable names",
                     "Names only; no values appear anywhere in this document. The typed registry is `src/account_intelligence/config.py` (about 170 settings)."))
    sec = _section(f"{S}/audit_data_infra.md", r"### 3\.7", r"### 3\.8")
    h.append('<div class="md">' + _md(sec, 1) + "</div>")
    h.append(end_chapter())

    h.append(chapter("E", "Configuration files: which file controls what",
                     "From the previous guide (29 October). Treat the token-guard and slice-limit statements with the corrections in chapter 23."))
    sec = _section(f"{REPO}/docs/guide/04-config-map.md", r"## A\.")
    h.append('<div class="md">' + _md(sec, 1) + "</div>")
    h.append(end_chapter())

    h.append(chapter("F", "Python dependencies and import contracts",
                     "The full runtime dependency table and the 17 import-layer rules."))
    sec = _section(f"{S}/audit_data_infra.md", r"### 1\.2", r"### 1\.4")
    h.append('<div class="md">' + _md(sec, 1) + "</div>")
    h.append(end_chapter())

    h.append(chapter("G", "Prompts, model routing, caching and guards in detail",
                     "From the prompt and model audit: how a model is reached, the routing ladder, every research, Ask and file prompt with its guards, the owner's skill, caching and cost recording, and how code stops the AI from grading."))
    sec = _section(f"{S}/audit_prompts.md", r"## 2\.", r"## 12\.")
    h.append('<div class="md">' + _md(sec, 1) + "</div>")
    h.append(end_chapter())

    h.append(chapter("H", "Code-level traces of the engine",
                     "File and line references for the main flows, from the engine audit. Use these when you open the code. Diagram source blocks are left out because the figures in the chapters show the same flows."))
    parts = []
    for a, b in ((r"### A\.1", r"### A\.2"), (r"### A\.3", r"### A\.4"), (r"### B\.2", r"### B\.10"), (r"### C\.1", r"### C\.3"),
                 (r"### D\.1", r"### D\.3"), (r"### E\.1", r"### E\.4"), (r"### F\.1", r"### F\.4")):
        parts.append(_section(f"{S}/audit_engine.md", a, b))
    txt = "\n\n".join(parts)
    txt = re.sub(r"```mermaid.*?```", "", txt, flags=re.S)
    h.append('<div class="md">' + _md(txt, 2) + "</div>")
    h.append(end_chapter())
    return "\n".join(h)
