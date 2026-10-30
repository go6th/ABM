"""Stable keys shared by every builder: canonical URL, host, registrable domain, posting ID and
normalised names. Pure functions; no account knowledge."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# query parameters that only track a visit, never select a page
_TRACKING = re.compile(
    r"^(utm_.*|gclid|fbclid|mc_[a-z]+|ref|ref_src|source|src|cmpid|sc_[a-z]+)$", re.I
)
# second-level suffixes where the registrable domain has three labels
_SECOND_LEVEL = frozenset(
    {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "co.nz", "co.in", "co.jp", "com.br",
     "com.mx", "co.za", "com.sg", "com.hk", "com.cn", "com.tr"}
)  # fmt: skip
_REQ = re.compile(r"\b((?:JR|R|REQ|REQ-|J)[-_ ]?\d{3,}|\d{5,})\b", re.I)
_TRAILING_ID = re.compile(r"/(\d{4,})/?$")
_ID_BEFORE_SLUG = re.compile(r"/(\d{4,})(?:/[^/]*)?$")
_WORKDAY = re.compile(r"[_/-](R-?\d{3,})\b", re.I)
_WS = re.compile(r"\s+")


def host_of(url: str) -> str:
    """Lower-case host of ``url`` without ``www.`` and port; ``""`` when it has none."""
    try:
        host = urlsplit(url.strip()).hostname or ""
    except ValueError:
        return ""
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def registrable(host: str) -> str:
    """The registrable domain of a host (``jobs.example.co.uk`` -> ``example.co.uk``)."""
    labels = [p for p in host.lower().strip(".").split(".") if p]
    if len(labels) <= 2:
        return ".".join(labels)
    tail2 = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if tail2 in _SECOND_LEVEL else tail2


def canonical_url(url: str) -> str:
    """The URL with scheme and host lower-cased, ``www.`` dropped, the fragment and tracking
    parameters removed, remaining parameters sorted and a trailing slash trimmed (not at the
    root). ``""`` stays ``""``."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw if "://" in raw else "https://" + raw)
    except ValueError:
        return raw
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    try:
        port = parts.port
    except ValueError:  # text that only looks like a URL ("Part: Foo bar")
        return raw
    if port and port not in (80, 443):
        host = f"{host}:{port}"
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not _TRACKING.match(k)
        )
    )
    path = parts.path.rstrip("/") if len(parts.path) > 1 else parts.path
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme
    return urlunsplit((scheme, host, path, query, ""))


def _path_of(url: str) -> str:
    try:
        return urlsplit(url or "").path
    except ValueError:
        return ""


def posting_id(url: str, requisition: str = "") -> str:
    """The posting's own ID: a Workday style ``R-<n>`` in the URL or requisition, else the numeric
    id that ends (or precedes the slug of) a careers URL path, else the requisition, else the
    canonical URL. The same posting seen at two URLs gets one ID when either carries it."""
    path = _path_of(url)
    for chunk in (requisition or "", path):
        m = _WORKDAY.search(chunk) or re.fullmatch(r"\s*(R-?\d{3,})\s*", chunk, re.I)
        if m:
            return re.sub(r"^R-?", "R-", m.group(1).upper())
    m = _TRAILING_ID.search(path) or _ID_BEFORE_SLUG.search(path)
    if m:
        return m.group(1)
    if requisition.strip():
        return requisition.strip()
    return canonical_url(url)


def fold(text: str) -> str:
    """Accent-folded, lower-cased, whitespace-collapsed text (for keys, never for display)."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _WS.sub(" ", plain).strip().lower()


def norm_name(name: str) -> str:
    """A person name as a key: folded, letters and spaces only, honorifics and suffixes dropped."""
    cleaned = re.sub(r"[^a-z\s'-]", " ", fold(name))
    words = [
        w
        for w in cleaned.split()
        if w not in {"dr", "mr", "mrs", "ms", "prof", "jr", "sr", "ii", "iii", "iv", "phd", "mba"}
    ]
    return " ".join(words)


def norm_query(query: str) -> str:
    """A search query as a key: folded, punctuation and case ignored, word order kept."""
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", fold(query)).split())
