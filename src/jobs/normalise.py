"""Pure normalization helpers; no network, clock, or environment access."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobs.models import RawPosting

_TRACKING = {"fbclid", "gclid", "ref", "source", "utm_source", "utm_medium", "utm_campaign"}
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)")
_TAG = re.compile(r"<[^>]*>")


def canonical_url(url: str) -> str:
    """Normalize a returned URL and remove common tracking parameters."""
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


def clean_text(text: str, max_chars: int = 100_000) -> str:
    """Strip markup/control characters and PII, then enforce a hard size cap."""
    cleaned = _TAG.sub(" ", text)
    cleaned = "".join(c for c in cleaned if c in "\n\t" or unicodedata.category(c)[0] != "C")
    cleaned = _PHONE.sub("[redacted]", _EMAIL.sub("[redacted]", cleaned))
    return " ".join(cleaned.split())[:max_chars]


def normalise_status(status: str | None) -> str:
    """Map only explicit status values to open/closed; preserve unknown otherwise."""
    if status is None:
        return "unknown"
    value = status.strip().lower()
    if value in {"open", "active", "published"}:
        return "open"
    if value in {"closed", "inactive", "expired", "filled"}:
        return "closed"
    return "unknown"


def posting_key(raw: RawPosting, *, with_strength: bool = False) -> str | tuple[str, bool]:
    """Return ID, canonical URL, or content-fingerprint identity in that order."""
    if raw.source_id:
        result = ("id:" + raw.source_id, False)
    elif canonical_url(raw.url):
        result = ("url:" + canonical_url(raw.url), False)
    else:
        normalized = clean_text(raw.title + " " + (raw.description or "")).casefold()
        result = ("weak:" + hashlib.sha256(normalized.encode()).hexdigest(), True)
    return result if with_strength else result[0]
