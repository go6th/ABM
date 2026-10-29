"""Whole-word cue matching shared by level, lane, exclusion and review rules. Pure.

Matching reuses the job pipeline's whole-word pattern (``jobs.match.term_pattern``: case
insensitive, never inside another word) on text sanitised by the grading engine's ``sanitise``
(control and zero-width characters stripped, NFKC, whitespace collapsed), so a cue written with
one space matches a title written with several.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache

from account_intelligence.grading.spans import sanitise
from jobs.match import term_pattern

MAX_TEXT = 2000


def clean(text: str | None, max_chars: int = MAX_TEXT) -> str:
    """The comparison form of an untrusted field ('' for ``None``)."""
    return sanitise(text or "", max_chars)


def find_cue(text: str, cues: Iterable[str]) -> str | None:
    """The substring of ``text`` matched by the first cue that appears as whole words, or
    ``None``. ``text`` should already be ``clean``."""
    for cue in cues:
        match = term_pattern(cue).search(text)
        if match is not None:
            return match.group(0)
    return None


@lru_cache(maxsize=512)
def fragment_pattern(fragment: str) -> re.Pattern[str]:
    """Case-insensitive pattern for one regular-expression fragment of a rubric rule, anchored
    on word starts and ends (the fragment may end in a stem such as ``technolog\\w*``)."""
    return re.compile(r"(?<!\w)(?:" + fragment + r")(?!\w)", re.IGNORECASE)


def find_pattern(text: str, patterns: Iterable[str]) -> str | None:
    """The substring of ``text`` matched by the first fragment that matches, or ``None``."""
    for fragment in patterns:
        match = fragment_pattern(fragment).search(text)
        if match is not None:
            return match.group(0)
    return None


def find_any(text: str, cues: Iterable[str], patterns: Iterable[str] = ()) -> str | None:
    """The first match of the whole-word ``cues``, else of the ``patterns`` (``None`` if none)."""
    return find_cue(text, cues) or find_pattern(text, patterns)
