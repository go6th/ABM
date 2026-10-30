"""One check for every address a client file links to (security review 2026-10-29, F10).

``is_safe_http_url`` is true only for an absolute ``http`` or ``https`` address that has a host,
carries no credentials (``user:pw@host``), no whitespace anywhere (a trailing newline included:
``$`` in a regular expression matches before it), no control, zero-width or bidirectional
characters, no backslash, and is at most ``MAX_URL`` characters (Excel's own hyperlink limit is
2,079). The render models (``exports.models.Url``), the Word contract (``Href``) and the
workbook writer (``Book.link``, ``Book.body``) all call it, so a value that reaches a hyperlink
target by a path that skipped the model is refused there as well. Pure.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

MAX_URL = 2048
# whitespace, C0/C1 controls, zero-width, bidi embedding/override/isolate controls, line and
# paragraph separators, the byte-order mark, soft hyphen
_FORBIDDEN = re.compile(
    "[\\s\\x00-\\x1f\\x7f-\\x9f\\u00ad\\u200b-\\u200f\\u2028-\\u202e\\u2060-\\u206f\\ufeff\\\\]"
)


def is_safe_http_url(value: object) -> bool:
    """True for an absolute http(s) address without credentials, whitespace, control or bidi
    characters, within ``MAX_URL`` characters."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL:
        return False
    if _FORBIDDEN.search(value):
        return False
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(host) and "@" not in parts.netloc


def require_safe_http_url(value: object, what: str) -> str:
    """The address, or ``ValueError`` naming ``what`` (never the address)."""
    if not is_safe_http_url(value):
        raise ValueError(f"{what} is an absolute http(s) address without spaces or credentials")
    return str(value)
