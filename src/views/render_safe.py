"""Untrusted text, made safe for each place it is rendered (VW4). Pure.

Source quotes, titles, names and URLs come from the open web (or a tenant's systems) and are
hostile until proven otherwise:

* ``text_cell``: control characters and bidirectional overrides stripped, length capped; the
  result is plain text (HTML is escaped by the renderer: React text nodes in the web app,
  ``html_text`` for anything rendered on the server).
* ``html_text``: HTML-escaped text (``&``, ``<``, ``>``, quotes).
* ``safe_link``: only absolute ``http`` and ``https`` URLs without credentials survive; anything
  else (``javascript:``, ``data:``, ``vbscript:``, relative, protocol-relative) becomes None, so
  no link is rendered and nothing is fetched.
* ``csv_cell`` / ``sheet_cell``: a value a spreadsheet would evaluate (leading ``=``, ``+``,
  ``-``, ``@``, tab or carriage return, also after leading whitespace) is prefixed with an
  apostrophe so it is shown as text, never run (CSV and Excel formula injection).

GUARANTEE(test_render_safe_neutralises_every_payload_in_the_corpus)
"""

from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import urlsplit

# Built from code points so no invisible or look-alike character ever sits in this source file.
_FORMULA_START = (
    "=",
    "+",
    "-",
    "@",
    "\t",
    "\r",
    *(chr(c) for c in (0xFF1D, 0xFF0B, 0xFF0D, 0xFF20)),
)
_BIDI = frozenset(
    chr(c)
    for c in (
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
        0x200E,
        0x200F,
    )
)
_ZWJ, _ELLIPSIS, _NBSP = chr(0x200D), chr(0x2026), chr(0xA0)
_SPACE = re.compile(r"[ \t\r\n\f\v]+")


def _clean(value: str) -> str:
    out = []
    for ch in value:
        if ch in _BIDI:
            continue
        if ch in "\t\n\r":
            out.append(" ")
            continue
        if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn") and ch != _ZWJ:
            continue
        out.append(ch)
    return "".join(out)


def text_cell(value: object, cap: int = 2000) -> str:
    """Plain, single-line text with control and direction-override characters removed."""
    if value is None:
        return ""
    cleaned = _SPACE.sub(" ", _clean(str(value))).strip()
    return cleaned if len(cleaned) <= cap else cleaned[: cap - 1] + _ELLIPSIS


def html_text(value: object, cap: int = 2000) -> str:
    """``text_cell`` escaped for HTML."""
    return html.escape(text_cell(value, cap), quote=True)


def safe_link(value: object) -> str | None:
    """The URL when it is an absolute http(s) URL with a host and no credentials, else None."""
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or len(raw) > 2048 or any(c.isspace() or ord(c) < 32 for c in raw):
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if raw.startswith("//") or "\\" in raw:
        return None
    return raw


def _neutral(value: str) -> str:
    stripped = value.lstrip(" " + _NBSP)
    if stripped.startswith(_FORMULA_START) or value.startswith(_FORMULA_START):
        return "'" + value
    return value


def csv_cell(value: object, cap: int = 4000) -> str:
    """A CSV cell that no spreadsheet will evaluate."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return _neutral(text_cell(value, cap))


def sheet_cell(value: object, cap: int = 4000) -> object:
    """An Excel cell value: numbers and booleans stay typed; text is neutralised like CSV."""
    if value is None or isinstance(value, bool | int | float):
        return value
    return _neutral(text_cell(value, cap))
