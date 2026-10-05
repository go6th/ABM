"""Deterministic platform sentences (J3). Pure.

Sentences are segmented in the STORED text (``normalise.stored_text``): a sentence ends at
``.``, ``!`` or ``?`` followed by whitespace or the end of the text, or at a line break. A
platform is named only by a
whole-word, case-insensitive lexicon term. Each hit yields a ``PlatformSentence`` whose offsets
index the stored text, whose hash is of that verbatim span, and whose ``text`` is the span with
PII removed. No model writes or paraphrases a sentence.
"""

from __future__ import annotations

import hashlib
import re

from jobs.match import term_pattern
from jobs.models import PlatformSentence
from jobs.normalise import strip_pii

# A sentence runs until terminal punctuation followed by whitespace or the end, or a line break.
# Punctuation inside a word ("Salesforce.com", "jane.doe") does not end it.
_SENTENCE = re.compile(r"(?:[^\n.!?]|[.!?]+(?!\s|$))+[.!?]*")


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` of each sentence in ``text``, whitespace trimmed."""
    spans = []
    for m in _SENTENCE.finditer(text):
        chunk = m.group()
        lead = len(chunk) - len(chunk.lstrip())
        start, end = m.start() + lead, m.start() + len(chunk.rstrip())
        if end > start and any(c.isalnum() for c in text[start:end]):
            spans.append((start, end))
    return spans


def extract_platform_sentences(
    text: str,
    platforms: tuple[str, ...],
    lexicon: dict[str, tuple[str, ...]],
    max_chars: int = 600,
) -> tuple[PlatformSentence, ...]:
    """Every sentence of ``text`` that names one of ``platforms``, per platform, in order."""
    out: list[PlatformSentence] = []
    spans = sentence_spans(text)
    for platform in platforms:
        patterns = [term_pattern(t) for t in (platform, *lexicon.get(platform, ()))]
        for start, end in spans:
            span = text[start:end]
            if any(p.search(span) for p in patterns):
                out.append(
                    PlatformSentence(
                        platform=platform,
                        text=strip_pii(span)[:max_chars],
                        start=start,
                        end=end,
                        sha256=hashlib.sha256(span.encode()).hexdigest(),
                    )
                )
    return tuple(out)
