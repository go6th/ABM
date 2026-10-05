"""Lexicon matching on whole words (J3, D-16). Pure.

A platform is "named" by a posting only when a lexicon term appears in the DESCRIPTION text. A
title-only hit is recorded separately: such postings are Lead candidates and are never
classified. Matching is case-insensitive and on word boundaries, so a term never matches inside
another word.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache


@lru_cache(maxsize=512)
def term_pattern(term: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive pattern for one lexicon term."""
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)


@dataclass(frozen=True)
class PlatformMatches:
    """Platforms named in the description, and platforms named only in the title."""

    description: tuple[str, ...]
    title_only: tuple[str, ...]


def match_platforms(
    title: str, description: str, lexicon: dict[str, tuple[str, ...]]
) -> PlatformMatches:
    """Match canonical platform names and configured aliases."""
    desc: list[str] = []
    title_hits: list[str] = []
    for platform, aliases in lexicon.items():
        patterns = [term_pattern(t) for t in (platform, *aliases)]
        if any(p.search(description) for p in patterns):
            desc.append(platform)
        elif any(p.search(title) for p in patterns):
            title_hits.append(platform)
    return PlatformMatches(tuple(sorted(desc)), tuple(sorted(title_hits)))
