"""Deterministic sentence spans for platform evidence."""

import hashlib
import re

from jobs.models import PlatformSentence
from jobs.normalise import clean_text


def extract_platform_sentences(
    text: str, platforms: tuple[str, ...], lexicon: dict[str, tuple[str, ...]]
) -> tuple[PlatformSentence, ...]:
    """Return verbatim sentence spans containing each named platform term."""
    cleaned = clean_text(text)
    spans: list[PlatformSentence] = []
    for match in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)", cleaned):
        sentence = match.group().strip()
        start = match.start() + len(match.group()) - len(match.group().lstrip())
        for platform in platforms:
            terms = (platform, *lexicon.get(platform, ()))
            if any(
                re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", sentence, re.I)
                for term in terms
            ):
                spans.append(
                    PlatformSentence(
                        platform=platform,
                        text=sentence,
                        start=start,
                        end=start + len(sentence),
                        sha256=hashlib.sha256(sentence.encode()).hexdigest(),
                    )
                )
    return tuple(spans)
