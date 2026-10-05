"""Pure normalisation: canonical URLs, posting keys, markup and PII stripping, status mapping.

No network, clock or environment access. Two text forms exist:

* ``stored_text``: markup and control characters removed, entities decoded, runs of spaces
  collapsed, line breaks kept. This is what the payload store holds as the posting text and what
  platform-sentence offsets index (J3).
* derived fields (title, sentence text, class evidence): ``stored_text`` with email addresses and
  phone numbers replaced by ``[redacted]`` (PII proposal, D-06 pending counsel).
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobs.models import RawPosting

_TRACKING = {"fbclid", "gclid", "ref", "source", "utm_source", "utm_medium", "utm_campaign"}
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)")
_TAG = re.compile(r"<[^>]*>")
_BLOCK = re.compile(r"</?(?:p|div|li|br|h[1-6]|tr|ul|ol)\b[^>]*>", re.I)
_SPACES = re.compile(r"[ \t\f\v]+")


def canonical_url(url: str) -> str:
    """Normalise a returned URL (lower-case scheme and host, no fragment, no tracking query).
    Returns ``""`` for anything that is not an absolute http(s) URL."""
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return ""
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in _TRACKING
        ]
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def stored_text(text: str, max_chars: int = 100_000) -> str:
    """Markup-free posting text with line breaks kept (the form sentence offsets index)."""
    marked = _BLOCK.sub("\n", text)
    plain = html.unescape(_TAG.sub(" ", marked))
    plain = unicodedata.normalize("NFKC", plain)
    plain = "".join(c for c in plain if c == "\n" or unicodedata.category(c)[0] != "C")
    lines = (_SPACES.sub(" ", line).strip() for line in plain.split("\n"))
    return "\n".join(line for line in lines if line)[:max_chars]


def strip_pii(text: str) -> str:
    """Replace email addresses and phone numbers (derived fields only)."""
    return _PHONE.sub("[redacted]", _EMAIL.sub("[redacted]", text))


def clean_text(text: str, max_chars: int = 100_000) -> str:
    """A derived, single-line, PII-free form (titles, evidence)."""
    return " ".join(strip_pii(stored_text(text, max_chars)).split())


def normalise_status(status: str | None) -> str:
    """Map only explicit provider values to open or closed; anything else is unknown."""
    if status is None:
        return "unknown"
    value = status.strip().lower()
    if value in {"open", "active", "published"}:
        return "open"
    if value in {"closed", "inactive", "expired", "filled"}:
        return "closed"
    return "unknown"


def posting_key(raw: RawPosting, *, with_strength: bool = False) -> str | tuple[str, bool]:
    """Provider job id, else canonical URL, else a content fingerprint flagged ``weak_key``.
    Never the title alone (J8)."""
    if raw.source_id:
        result = ("id:" + raw.source_id, False)
    elif canonical_url(raw.url):
        result = ("url:" + canonical_url(raw.url), False)
    else:
        normalized = clean_text(raw.title + " " + (raw.description or "")).casefold()
        result = ("weak:" + hashlib.sha256(normalized.encode()).hexdigest(), True)
    return result if with_strength else result[0]
