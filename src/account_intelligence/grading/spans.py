"""Deterministic URL and supporting-sentence checks (G3, G4) and sanitisation (G14).

* ``verify_url``: the normalised URL must be one this run stored, http(s), no credentials,
  and its host must equal the stored source's host.
* ``verify_span``: the sentence must appear verbatim and contiguous in the stored text of a
  source at that URL after NFKC and whitespace normalisation (case preserved), within the
  configured length bounds. Full page reads are preferred when several fetches match.
* ``value_support``: the claim's normalised value must appear in the sentence.
* ``reanchor_sentence``: when it does not, the sentence of the same stored text that carries the
  value and stands for the cited one (a neighbour or a close paraphrase), checked the same way.
* ``check_hostile``: a canary or an instruction echoed in a sentence makes the source hostile.
The engine never reads raw page text except through these functions.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from urllib.parse import urlsplit

from account_intelligence.grading.config import GradingConfig, Normaliser
from account_intelligence.grading.errors import (
    HostileContent,
    SchemaViolation,
    SpanNotFound,
    UrlNotInRun,
    ValueNotInSentence,
)
from account_intelligence.grading.ports import RunSource
from account_intelligence.urls import normalize_url

# Control, zero-width and bidirectional characters, written as escapes (never literally in source).
_CONTROL = re.compile(
    "[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f-\\x9f\u200b-\u200f\u2028\u2029\ufeff]"
)
_ORIGIN_RANK = {
    "full_page_read": 0,
    "cached_copy": 1,
    "search_snippet": 2,
    "feed_item": 3,
    "broker_record": 4,
}


def normalise_ws(text: str) -> str:
    """NFKC and whitespace collapse; case preserved (verbatim comparison)."""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def casefold_text(text: str) -> str:
    """NFKC, whitespace-collapsed, case-folded comparison form."""
    return normalise_ws(text).casefold()


def sanitise(text: str, max_chars: int) -> str:
    """Strip control and zero-width characters, NFKC, collapse whitespace, cap length."""
    cleaned = normalise_ws(_CONTROL.sub(" ", text))
    return cleaned[:max_chars]


def check_hostile(text: str, cfg: GradingConfig) -> None:
    """Raise HostileContent when a canary or instruction marker appears (G14)."""
    folded = casefold_text(text)
    for marker in cfg.spans.canary_markers:
        if marker.casefold() in folded:
            raise HostileContent("canary echoed")
    for marker in cfg.spans.instruction_markers:
        if marker.casefold() in folded:
            raise HostileContent("instruction echoed")


def verify_url(url: str, sources: Sequence[RunSource]) -> tuple[str, tuple[RunSource, ...]]:
    """Return the normalised URL and this run's stored sources at it (G3)."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise UrlNotInRun("scheme must be http or https")
    if parts.username is not None or parts.password is not None:
        raise UrlNotInRun("credentials in URL")
    try:
        normalised = normalize_url(url)
    except ValueError as error:
        raise UrlNotInRun("url does not normalise") from error
    host = (parts.hostname or "").rstrip(".").lower()
    matches = tuple(s for s in sources if s.url_normalized == normalised)
    if not matches:
        raise UrlNotInRun("url was not returned by any call in this run")
    if any(s.host.lower().rstrip(".") != host for s in matches):
        raise UrlNotInRun("host differs from the registered source host")
    return normalised, matches


def verify_span(sentence: str, candidates: Sequence[RunSource], cfg: GradingConfig) -> RunSource:
    """Return the stored source whose text contains the sentence verbatim (G4)."""
    clean = normalise_ws(sentence)
    if not cfg.spans.min_sentence_chars <= len(clean) <= cfg.spans.max_sentence_chars:
        raise SpanNotFound("supporting sentence length out of bounds")
    ordered = sorted(candidates, key=lambda s: (_ORIGIN_RANK[s.origin], s.call_ref, s.source_ref))
    for source in ordered:
        if clean in normalise_ws(source.text):
            return source
    raise SpanNotFound("sentence not found verbatim in the cited source")


_NUMBER_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[a-z%$€£¥₹]+")


def _tokens(text: str) -> list[str]:
    return _NUMBER_TOKEN.findall(casefold_text(text))


def _contiguous(needle: list[str], haystack: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


def figure_in_text(figure: str, text: str) -> bool:
    """Numbers with their adjacent unit tokens appear contiguously (an altered figure fails)."""
    return _contiguous(_tokens(figure), _tokens(text))


def value_support(value: str, sentence: str, normaliser: Normaliser) -> None:
    """The claim's normalised value must appear in the sentence (G4)."""
    if normaliser == "number":
        if not figure_in_text(value, sentence):
            raise ValueNotInSentence("figure not in sentence")
        return
    needle = re.findall(r"\w+", casefold_text(value))
    hay = re.findall(r"\w+", casefold_text(sentence))
    if not _contiguous(needle, hay):
        raise ValueNotInSentence("value not in sentence")


def normalise_value(value: str, normaliser: Normaliser) -> str:
    """Normalise a claimed value in code (never in the model)."""
    if normaliser == "number":
        return " ".join(_tokens(value))
    if normaliser == "iso_date":
        return casefold_text(value)
    if normaliser == "casefold_text":
        return casefold_text(value)
    return normalise_ws(value)


def bounded(text: str, max_chars: int, what: str) -> str:
    """Sanitised text within a length bound, or SchemaViolation."""
    clean = sanitise(text, max_chars + 1)
    if len(clean) > max_chars:
        raise SchemaViolation(f"{what} longer than {max_chars} characters")
    return clean


# Re-anchoring (G4 repair) ------------------------------------------------------------------------

# A sentence of a normalised text ends at ``.``, ``!`` or ``?`` followed by a space (the text is
# whitespace collapsed) or at the end; punctuation inside a word ("Salesforce.com") does not end it.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=\S)")
# the share of the cited sentence's words a replacement sentence must carry when the cited
# sentence is a paraphrase rather than a neighbour of the one that holds the value
MIN_SHARED_WORDS = 0.3


def split_sentences(text: str) -> list[str]:
    """The sentences of a whitespace-normalised text, in order (each verbatim in the text)."""
    return [s for s in _SENTENCE_END.split(text) if s.strip()]


def supports(value: str, sentence: str, normaliser: Normaliser) -> bool:
    """True when ``value_support`` accepts the sentence for the value."""
    try:
        value_support(value, sentence, normaliser)
    except ValueNotInSentence:
        return False
    return True


def shared_words(a: str, b: str) -> float:
    """The share of ``a``'s distinct words that ``b`` also carries (case folded), 0 to 1."""
    wa = set(re.findall(r"\w+", casefold_text(a)))
    wb = set(re.findall(r"\w+", casefold_text(b)))
    return len(wa & wb) / len(wa) if wa else 0.0


def reanchor_sentence(
    value: str, sentence: str, source_text: str, normaliser: Normaliser, cfg: GradingConfig
) -> str | None:
    """A sentence of ``source_text`` that carries ``value`` and stands for the cited one, or None.

    A model's cited sentence is often the right passage with the figure one sentence over, or a
    close paraphrase of the sentence that holds it; every such claim was dropped
    ``value_not_in_sentence`` (staging 2026-11-04: 486 of a run's claims). The replacement is
    looked for in this order: the cited sentence joined with its neighbour on either side (the
    value one sentence over), then the single sentence of the text that shares the most words
    with the cited one (a paraphrase), at least ``MIN_SHARED_WORDS`` of them. Every candidate is
    verbatim in the normalised text, within the configured length bounds and carries the value
    (``value_support``), so G4 holds for it exactly as for a sentence the model quoted right."""
    text = normalise_ws(source_text)
    clean = normalise_ws(sentence)
    lo, hi = cfg.spans.min_sentence_chars, cfg.spans.max_sentence_chars
    parts = split_sentences(text)
    if not parts or not clean:
        return None

    def fits(candidate: str) -> bool:
        return (
            lo <= len(candidate) <= hi
            and candidate in text
            and supports(value, candidate, normaliser)
        )

    at = next((i for i, p in enumerate(parts) if clean in p or p in clean), None)
    if at is not None:
        for first, last in ((at - 1, at), (at, at + 1), (at - 1, at + 1)):
            if first < 0 or last >= len(parts):
                continue
            joined = " ".join(parts[first : last + 1])
            if fits(joined):
                return joined
    best = max(
        ((shared_words(clean, p), -i, p) for i, p in enumerate(parts) if fits(p)),
        default=None,
    )
    if best is not None and best[0] >= MIN_SHARED_WORDS:
        return best[2]
    return None
