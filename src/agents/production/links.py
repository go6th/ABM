"""Which links of the account's own pages a research row follows (code only, no model).

A newsroom list names the releases of the last months, a release the ones around it, a
leadership or investor page the pages beside it. The owner's reference research walked the
newsroom and read some three hundred releases; a search returns at most ten results, so the
pages a search finds are the shallow part of the company's own site. ``followed_links`` picks,
from the text of pages already read, the links worth one more full read:

* the link must be on one of the account's own domains (``is_own``: the account's and the entity
  lock's), with no credentials in the address, over https;
* its path must carry one of the row's words (``FollowRule.include``): a newsroom row follows
  paths with news, press or release in them, never a careers or cart link;
* an address that carries a date (``/2023/05/``, ``2023-05-17``) older than the row's window is
  left out; an address with no date stays;
* a page already read (``read``) and a file that is not a page (image, style, script, media,
  archive) are left out;
* at most ``FollowRule.per_query`` links, in the order the pages list them (a newsroom lists the
  newest first), once each.

The link text is never passed on: only the address is chosen, by this code. A hostile page can
therefore only point the research at another page of the account's own domains, which is read
and checked like any other page of the account (off-account and hostile text are dropped when
the page is read for facts).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import date
from urllib.parse import parse_qsl, urldefrag, urlencode, urljoin, urlsplit, urlunsplit

# a markdown link target, an href, or a bare address in a page's text
_TARGET = re.compile(r"""\]\(\s*<?([^)\s>]+)|href=["']([^"']+)["']|(https?://[^\s<>"'\])]+)""")
_DATE = re.compile(r"(?<!\d)(20\d{2})[-/_]?(0[1-9]|1[0-2])?(?:[-/_]?(0[1-9]|[12]\d|3[01]))?(?!\d)")
_NOT_PAGES = frozenset(
    [
        "jpg",
        "jpeg",
        "png",
        "gif",
        "svg",
        "webp",
        "ico",
        "css",
        "js",
        "json",
        "xml",
        "mp3",
        "mp4",
        "mov",
        "avi",
        "webm",
        "zip",
        "gz",
        "tar",
        "woff",
        "woff2",
        "ttf",
        "eot",
    ]
)
_TRACKING = ("utm_", "fbclid", "gclid", "mc_", "_hs", "hsa_", "ref")
MAX_ADDRESS = 400


def _clean(url: str) -> str | None:
    """The address without fragment and tracking parameters; None when it is not an https
    address with a host and no credentials."""
    parts = urlsplit(urldefrag(url.strip())[0])
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    try:
        parts.port  # noqa: B018 - a malformed port raises ValueError
    except ValueError:
        return None
    keep = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(_TRACKING)]
    path = parts.path or "/"
    return urlunsplit(("https", parts.netloc.lower(), path, urlencode(keep), ""))


def _same(url: str) -> str:
    """The comparison key of an address: no scheme, no ``www``, no trailing slash."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    return f"{host}{parts.path.rstrip('/')}?{parts.query}"


def _dated_before(path: str, floor: date) -> bool:
    """True when the address carries a date and that date is before ``floor``. A year alone is
    compared by year, a year and month by month."""
    for match in _DATE.finditer(path):
        year = int(match.group(1))
        month = int(match.group(2)) if match.group(2) else None
        day = int(match.group(3)) if match.group(3) else None
        if not (2000 <= year <= 2100):
            continue
        if month is None:
            return year < floor.year
        return date(year, month, day or 28) < date(floor.year, floor.month, 1)
    return False


def followed_links(
    pages: Iterable[tuple[str, str]],
    is_own: Callable[[str], bool],
    rule: object,
    read: set[str],
    floor: date | None,
) -> list[str]:
    """The addresses to read next (see the module docstring). ``pages`` is ``(address, text)``
    of the pages already read; ``rule`` has ``include`` (words) and ``per_query`` (a count)."""
    include = tuple(str(w).lower() for w in getattr(rule, "include", ()))
    cap = int(getattr(rule, "per_query", 0))
    seen = {_same(u) for u in read}
    out: list[str] = []
    if cap <= 0 or not include:
        return out
    for base, text in pages:
        for match in _TARGET.finditer(text):
            target = next(g for g in match.groups() if g)
            if target.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            url = _clean(urljoin(base, target))
            if url is None or len(url) > MAX_ADDRESS or not is_own(url):
                continue
            path = urlsplit(url).path.lower()
            if path.rsplit(".", 1)[-1] in _NOT_PAGES and "." in path.rsplit("/", 1)[-1]:
                continue
            if not any(word in path for word in include):
                continue
            if floor is not None and _dated_before(path, floor):
                continue
            key = _same(url)
            if key in seen:
                continue
            seen.add(key)
            out.append(url)
            if len(out) >= cap:
                return out
    return out
