"""Whole-word cue matching shared by level, lane, exclusion and review rules. Pure.

Matching reuses the job pipeline's whole-word pattern (``jobs.match.term_pattern``: case
insensitive, never inside another word) on text sanitised by the grading engine's ``sanitise``
(control and zero-width characters stripped, NFKC, whitespace collapsed), so a cue written with
one space matches a title written with several.
"""

from __future__ import annotations

from collections.abc import Iterable

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
