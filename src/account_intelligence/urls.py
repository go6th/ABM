"""URL normalisation and hashing: the dedupe key for sources, profiles and postings.

Two URLs that point at the same page must normalise to the same string, and the
string must be stable across releases: changing these rules changes url_hash for
new rows and splits a source into two codes. Treat any rule change as a data
migration.
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}

# Query parameters that identify a campaign or click, never the page itself.
TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "_hsenc",
        "_hsmi",
        "yclid",
    }
)
TRACKING_PREFIXES = ("utm_",)


def _is_tracking(key: str) -> bool:
    lowered = key.lower()
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PREFIXES)


def normalize_url(url: str) -> str:
    """Return the canonical form of an http(s) URL.

    Lowercases scheme and host, drops credentials, default ports, the fragment and
    tracking parameters, sorts the remaining query parameters, and strips a trailing
    slash except on the root path. The scheme is kept: http and https can serve
    different content.

    Raises ValueError for anything that is not an absolute http(s) URL.
    """
    candidate = url.strip()
    if any(char.isspace() for char in candidate):
        raise ValueError(f"URL contains whitespace: {url!r}")
    parts = urlsplit(candidate)
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise ValueError(f"not an http(s) URL: {url!r}")
    host = (parts.hostname or "").rstrip(".")  # hostname is already lowercase
    if not host:
        raise ValueError(f"URL has no host: {url!r}")
    try:
        port = parts.port
    except ValueError as error:
        raise ValueError(f"URL has an invalid port: {url!r}") from error
    netloc = host if port in (None, DEFAULT_PORTS[scheme]) else f"{host}:{port}"

    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"

    params = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking(key)
    ]
    query = urlencode(sorted(params))
    return urlunsplit((scheme, netloc, path, query, ""))


def url_hash(normalized_url: str) -> str:
    """sha256 hex of an already-normalised URL; the database re-checks this value."""
    return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()
