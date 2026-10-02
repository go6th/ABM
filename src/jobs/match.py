"""Description-based platform matching with explicit title-only leads."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformMatches:
    description: tuple[str, ...]
    title_only: tuple[str, ...]


def _contains(text: str, term: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.IGNORECASE) is not None


def match_platforms(
    title: str, description: str, lexicon: dict[str, tuple[str, ...]]
) -> PlatformMatches:
    """Match canonical platform names and configured aliases in text."""
    desc: list[str] = []
    title_hits: list[str] = []
    for platform, aliases in lexicon.items():
        terms = (platform, *aliases)
        if any(_contains(description, term) for term in terms):
            desc.append(platform)
        elif any(_contains(title, term) for term in terms):
            title_hits.append(platform)
    return PlatformMatches(tuple(sorted(desc)), tuple(sorted(title_hits)))
