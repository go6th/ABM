"""Lexicon matching on whole words (J3, D-16). Pure.

A platform is "named" by a posting only when a lexicon term appears in the DESCRIPTION text. A
title-only hit is recorded separately: such postings are Lead candidates and are never
classified. Matching is case-insensitive and on word boundaries, so a term never matches inside
another word. A platform listed as case sensitive in the lexicon (``job_lanes.yaml``
``case_sensitive: true``) is matched in its own case only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache


@lru_cache(maxsize=512)
def term_pattern(term: str, case_sensitive: bool = False) -> re.Pattern[str]:
    """Whole-word pattern for one lexicon term: case-insensitive, or exact in case for a platform
    whose name is also a word (``SAP`` is not "a sap of"; the reference matches it that way)."""
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", flags)


@dataclass(frozen=True)
class PlatformMatches:
    """Platforms named in the description, and platforms named only in the title."""

    description: tuple[str, ...]
    title_only: tuple[str, ...]


def match_platforms(
    title: str,
    description: str,
    lexicon: dict[str, tuple[str, ...]],
    case_sensitive: frozenset[str] = frozenset(),
) -> PlatformMatches:
    """Match canonical platform names and configured aliases; every term of a platform in
    ``case_sensitive`` must match in the case the lexicon spells it."""
    desc: list[str] = []
    title_hits: list[str] = []
    for platform, aliases in lexicon.items():
        exact = platform in case_sensitive
        patterns = [term_pattern(t, exact) for t in (platform, *aliases)]
        if any(p.search(description) for p in patterns):
            desc.append(platform)
        elif any(p.search(title) for p in patterns):
            title_hits.append(platform)
    return PlatformMatches(tuple(sorted(desc)), tuple(sorted(title_hits)))
