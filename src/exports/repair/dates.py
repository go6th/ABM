"""Real dates from the source: a URL path, a title or a sentence. Strict on purpose (spec rule 3):
a number that merely looks like a date is not one. Nothing here reads the clock."""

from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urlsplit

MIN_YEAR = 1990
MAX_YEAR = 2100

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}  # fmt: skip
_MONTH = r"(?P<m>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")"

# /2026/10/12/  /2026-10-12-  /2026_10_12  (one separator kind, 1-2 digit month and day)
_URL_SEPARATED = re.compile(r"(?<!\d)(\d{4})([/_-])(\d{1,2})\2(\d{1,2})(?!\d)")
# /20261012 (exactly eight digits)
_URL_COMPACT = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")

_ISO = re.compile(r"(?<!\d)(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})(?!\d)")
_DAY_FIRST = re.compile(
    rf"(?<![\w/])(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\b\.?,?\s+(?P<y>\d{{4}})(?!\d)",
    re.I,
)
_MONTH_FIRST = re.compile(
    rf"\b{_MONTH}\b\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?!\d),?\s+(?P<y>\d{{4}})(?!\d)", re.I
)


def _make(year: int, month: int, day: int) -> date | None:
    """A date only when the year is in range and the month and day exist."""
    if not MIN_YEAR <= year <= MAX_YEAR:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def date_from_url(url: str) -> date | None:
    """The publication date written in a URL path or query, or None. Only a four-digit year in
    1990..2100 with a real month and day counts, so ``/26/10/62156538/`` is not a date."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return None
    text = parts.path + ("?" + parts.query if parts.query else "")
    found: list[tuple[int, date]] = []
    for m in _URL_SEPARATED.finditer(text):
        made = _make(int(m.group(1)), int(m.group(3)), int(m.group(4)))
        if made:
            found.append((m.start(), made))
    for m in _URL_COMPACT.finditer(text):
        made = _make(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if made:
            found.append((m.start(), made))
    return min(found, key=lambda pair: pair[0])[1] if found else None


def date_from_text(text: str) -> date | None:
    """The first plausible date in free text: ISO (2026-10-12), ``12 October 2026``,
    ``October 12, 2026`` or ``Oct 12, 2026``. A month and year alone is not a date."""
    body = text or ""
    found: list[tuple[int, date]] = []
    for m in _ISO.finditer(body):
        made = _make(int(m["y"]), int(m["mo"]), int(m["d"]))
        if made:
            found.append((m.start(), made))
    for pattern in (_DAY_FIRST, _MONTH_FIRST):
        for m in pattern.finditer(body):
            made = _make(int(m["y"]), _MONTHS[m["m"].lower()], int(m["d"]))
            if made:
                found.append((m.start(), made))
    return min(found, key=lambda pair: pair[0])[1] if found else None


def _as_date(candidate: object) -> date | None:
    if isinstance(candidate, datetime):
        candidate = candidate.date()
    if isinstance(candidate, date):
        return candidate if MIN_YEAR <= candidate.year <= MAX_YEAR else None
    if isinstance(candidate, str):
        return date_from_text(candidate)
    return None


def real_date(*candidates: object) -> date | None:
    """The first candidate that is a real date. A ``date`` or ``datetime`` counts when its year is
    in range; a string is read with ``date_from_text``; None and anything else are skipped."""
    for candidate in candidates:
        found = _as_date(candidate)
        if found:
            return found
    return None


def is_collection_date(
    published: date | datetime | None, collected: date | datetime | None
) -> bool:
    """True when the published date is the day the page was collected. The caller then treats the
    date as unknown unless the source itself proves it (spec rule 3)."""
    if published is None or collected is None:
        return False
    pub = published.date() if isinstance(published, datetime) else published
    col = collected.date() if isinstance(collected, datetime) else collected
    return pub == col
