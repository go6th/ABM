"""Canonical profile URLs (P3). Pure.

Every URL is first normalised by the platform's one URL rule (``account_intelligence.urls``:
lower-case scheme and host, no credentials, default port, fragment or tracking parameters, no
trailing slash). A URL on a configured profile host additionally gets ``https``, the canonical
host for every locale subdomain, no query at all (the identity is the path) and, when configured,
a case-folded path. Anything that is not an absolute http(s) URL yields ``None``: a person without
a canonical profile URL is never put in the list (P2).
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from account_intelligence.urls import normalize_url
from people.config import ProfileUrlRule


def is_profile_host(host: str, rule: ProfileUrlRule) -> bool:
    """True for the configured profile host and any of its subdomains."""
    return host == rule.host_suffix or host.endswith("." + rule.host_suffix)


def canonical_profile_url(url: str | None, rule: ProfileUrlRule) -> str | None:
    """The canonical form of a profile URL, or ``None`` when it is not a usable URL."""
    if not url:
        return None
    try:
        normalised = normalize_url(url)
    except ValueError:
        return None
    parts = urlsplit(normalised)
    host = parts.hostname or ""
    if not is_profile_host(host, rule):
        return normalised
    path = parts.path.casefold() if rule.fold_path_case else parts.path
    path = path.rstrip("/") or "/"
    if path == "/":
        return None  # the site's home page identifies nobody
    return urlunsplit(("https", rule.canonical_host, path, "", ""))
