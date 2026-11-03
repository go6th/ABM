"""HTML helpers for the audit PDF: inline markup, tables, figures, callouts."""
from __future__ import annotations

import re
from html import escape

CHAPTERS: list[tuple[str, str]] = []  # (anchor, title) in order, filled by chapter()
_FIGS = {"n": 0}


def inline(s: str) -> str:
    """Escape text, then turn `code` and **bold** into HTML."""
    s = escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    return s


def p(text: str, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return f"<p{c}>{inline(text)}</p>"


def ul(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>"


def ol(items: list[str]) -> str:
    return "<ol>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ol>"


def h2(text: str) -> str:
    return f"<h3>{inline(text)}</h3>"


def chapter(num: str, title: str, lead: str = "") -> str:
    anchor = f"ch{len(CHAPTERS) + 1}"
    CHAPTERS.append((anchor, f"{num} {title}" if num else title))
    lead_html = f'<p class="lead">{inline(lead)}</p>' if lead else ""
    return (
        f'<section class="chapter" id="{anchor}"><h2 class="ch-title" data-ch="{anchor}">'
        f'<span class="ch-num">{escape(num)}</span> {inline(title)}</h2>{lead_html}'
    )


def end_chapter() -> str:
    return "</section>"


def part(title: str, blurb: str) -> str:
    return f'<section class="part"><div class="part-inner"><h1>{inline(title)}</h1><p>{inline(blurb)}</p></div></section>'


def table(headers: list[str], rows: list[list[str]], widths: list[int] | None = None, cls: str = "") -> str:
    cg = ""
    if widths:
        cg = "<colgroup>" + "".join(f'<col style="width:{w}%">' for w in widths) + "</colgroup>"
    th = "".join(f"<th>{inline(h)}</th>" for h in headers)
    cell = lambda c: c if c.startswith("<") else inline(c)
    body = "".join("<tr>" + "".join(f"<td>{cell(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="t {cls}">{cg}<thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


def fig(svg: str, caption: str, wide: bool = False) -> str:
    _FIGS["n"] += 1
    n = _FIGS["n"]
    return (
        f'<figure class="fig{" wide" if wide else ""}">{svg}'
        f"<figcaption><b>Figure {n}.</b> {inline(caption)}</figcaption></figure>"
    )


def box(title: str, body_html: str, kind: str = "note") -> str:
    return f'<div class="box {kind}"><div class="box-title">{inline(title)}</div>{body_html}</div>'


def callout(title: str, text: str, kind: str = "note") -> str:
    return box(title, f"<p>{inline(text)}</p>", kind)


def sev(level: str) -> str:
    return f'<span class="sev {level.lower()}">{level}</span>'
