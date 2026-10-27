"""Canonical profile URLs (P3). Pure.

Every URL is first normalised by the platform's one URL rule (``account_intelligence.urls``:
lower-case scheme and host, no credentials, default port, fragment or tracking parameters, no
trailing slash). A URL on a configured profile host additionally gets ``https``, the canonical
host for every locale subdomain (``uk.``, ``de.``, ``m.``), no query at all (the identity is the
path) and, when configured, a case-folded path. On the profile host the path must be a person's
own page (``rule.person_paths``: ``/in/<slug>``, or the older public ``/pub/<name>/<a>/<b>/<c>``):
the locale or sub-page after the slug (``/in/jane-doe/en``, ``/in/jane-doe/details/experience``)
is cut, the mobile ``/mwlite`` prefix dropped, and percent-encoding made uniform (a name written
``j%C3%A9r%C3%B4me`` and ``jérôme`` is one person). A profile-host link written without its
scheme (``linkedin.com/in/jane-doe``, as some sources return it) is read as ``https``.

Anything that is not an absolute http(s) URL, the site's home page, and any other page on the
profile host (a company, a post, a job, the public directory search) yields ``None``: a person
without a canonical profile URL is never put in the list (P2), and two people who both point at
the same company page are never merged into one by it.

GUARANTEE(test_profile_url_regional_and_suffixed_forms_fold_to_one)
GUARANTEE(test_profile_url_non_person_pages_identify_nobody)
"""

from __future__ import annotations

import unicodedata
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from account_intelligence.urls import normalize_url
from people.config import ProfileUrlRule

# the mobile site's prefix before the person path (``/mwlite/in/<slug>``)
_MOBILE_PREFIXES = frozenset({"mwlite"})
# ``/pub/dir/<first>/<last>`` is the public directory search, not a person
_PUB_DIRECTORY = "dir"
# ``/pub/<name>/<a>/<b>/<c>``: the old public form is five segments long
_PUB_SEGMENTS = 5
# characters kept literally when the path is re-encoded (letters and digits always are)
_PATH_SAFE = "/-_.~"


def is_profile_host(host: str, rule: ProfileUrlRule) -> bool:
    """True for the configured profile host and any of its subdomains."""
    return host == rule.host_suffix or host.endswith("." + rule.host_suffix)


def _with_scheme(url: str, rule: ProfileUrlRule) -> str:
    """``url`` with ``https://`` in front when it is a profile-host link written without a
    scheme; anything else unchanged (so a bare word is still not a URL)."""
    candidate = url.strip()
    if "://" in candidate:
        return candidate
    bare = candidate.removeprefix("//")
    host = bare.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].lower()
    return "https://" + bare if host and is_profile_host(host, rule) else candidate


def _person_path(path: str, rule: ProfileUrlRule) -> str | None:
    """The canonical path of a person's own page on the profile host, or ``None``."""
    decoded = unicodedata.normalize("NFC", unquote(path))
    if rule.fold_path_case:
        decoded = decoded.casefold()
    segments = [s for s in decoded.split("/") if s]
    if segments and segments[0].casefold() in _MOBILE_PREFIXES:
        segments = segments[1:]
    if len(segments) < 2:
        return None
    head = segments[0].casefold()
    if head not in rule.person_paths:
        return None
    if head == "pub":
        if segments[1].casefold() == _PUB_DIRECTORY:
            return None
        kept = segments[1:_PUB_SEGMENTS]
    else:
        kept = segments[1:2]  # the slug; a locale or sub-page after it is the same person
    return quote("/" + "/".join((head, *kept)), safe=_PATH_SAFE)


def canonical_profile_url(url: str | None, rule: ProfileUrlRule) -> str | None:
    """The canonical form of a profile URL, or ``None`` when it is not a usable URL."""
    if not url:
        return None
    try:
        normalised = normalize_url(_with_scheme(url, rule))
    except ValueError:
        return None
    parts = urlsplit(normalised)
    host = parts.hostname or ""
    if not is_profile_host(host, rule):
        return normalised
    path = _person_path(parts.path, rule)
    if path is None:
        return None  # the home page or a page that is not a person's own
    return urlunsplit(("https", rule.canonical_host, path, "", ""))
