"""URL normalisation and hashing: the dedupe key for sources, profiles and postings.

Two URLs that point at the same page must normalise to the same string, and the
string must be stable across releases: changing these rules changes url_hash for
new rows and splits a source into two codes. Treat any rule change as a data
migration.
"""

from __future__ import annotations

import hashlib
import re
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


# Query parameters that carry a secret (a signed link, an access token, a password): the address
# the archive stores, serves and prints never carries them. The page they point at is public, so
# the address without them still opens it (leakage review 2026-10-30, F10).
SECRET_PARAMS = frozenset(
    {
        "token",
        "key",
        "sig",
        "signature",
        "access_token",
        "id_token",
        "refresh_token",
        "api_key",
        "apikey",
        "password",
        "passwd",
        "secret",
        "x-amz-signature",
        "x-amz-credential",
        "x-amz-security-token",
        "x-goog-signature",
        "x-goog-credential",
    }
)


def strip_secrets(url: str) -> str:
    """``url`` without credentials (``user:password@``) and without the query parameters of
    ``SECRET_PARAMS``; everything else, including the order of the other parameters, is kept.
    An address that cannot be parsed is returned as it is (the callers refuse non-http(s)
    addresses on their own). Pure."""
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError for an invalid port
    except ValueError:
        return url
    if parts.scheme.lower() not in DEFAULT_PORTS or not host:
        return url
    netloc = parts.netloc
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    query = parts.query
    if query:
        kept = [
            pair for pair in query.split("&") if pair.split("=", 1)[0].lower() not in SECRET_PARAMS
        ]
        query = "&".join(kept)
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def url_hash(normalized_url: str) -> str:
    """sha256 hex of an already-normalised URL; the database re-checks this value."""
    return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()


# characters that never belong in an address a client file links to: controls, zero-width and
# bidirectional marks, line and paragraph separators, the byte-order mark, soft hyphen and the
# backslash (browsers read it as a slash)
_UNUSABLE = re.compile(
    "[\\x00-\\x1f\\x7f-\\x9f\\u00ad\\u200b-\\u200f\\u2028-\\u202e\\u2060-\\u206f\\ufeff\\\\]"
)
MAX_LINK = 2048


def clean_http_url(url: str | None) -> str | None:
    """The usable form of an address a client file links to, or None when it has none.

    Repairs what is only spelling: whitespace around the address is dropped, spaces inside it
    become ``%20``, the scheme and host are lower case. An address is refused (None) when it is
    not absolute http or https, has no host, carries credentials, holds a control, zero-width or
    bidirectional character (any other whitespace included) or a backslash, or is longer than
    ``MAX_LINK``. The path, query and fragment are otherwise left exactly as given. Pure.
    """
    if not isinstance(url, str):
        return None
    candidate = url.strip()
    if not candidate or _UNUSABLE.search(candidate.replace(" ", "")):
        return None
    candidate = candidate.replace(" ", "%20")
    try:
        parts = urlsplit(candidate)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError for an invalid port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS or not host or "@" in parts.netloc:
        return None
    netloc = parts.netloc.lower()
    cleaned = urlunsplit((scheme, netloc, parts.path, parts.query, parts.fragment))
    return cleaned if len(cleaned) <= MAX_LINK else None
