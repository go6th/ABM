"""Deterministic URL and supporting-sentence checks (G3, G4) and sanitisation (G14).

* ``verify_url``: the normalised URL must be one this run stored, http(s), no credentials,
  and its host must equal the stored source's host.
* ``verify_span``: the sentence must appear verbatim and contiguous in the stored text of a
  source at that URL after NFKC and whitespace normalisation (case preserved), within the
  configured length bounds. Full page reads are preferred when several fetches match.
* ``value_support``: the claim's normalised value must appear in the sentence.
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

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f​-‏  ﻿]")
_ORIGIN_RANK = {"full_page_read": 0, "cached_copy": 1, "search_snippet": 2, "feed_item": 3, "broker_record": 4}


def normalise_ws(text: str) -> str:
    """NFKC and whitespace collapse; case preserved (verbatim comparison)."""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def casefold_text(text: str) -> str:
    return normalise_ws(text).casefold()


def sanitise(text: str, max_chars: int) -> str:
    """Strip control and zero-width characters, NFKC, collapse whitespace, cap length."""
    cleaned = normalise_ws(_CONTROL.sub(" ", text))
    return cleaned[:max_chars]


def check_hostile(text: str, cfg: GradingConfig) -> None:
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
    clean = sanitise(text, max_chars + 1)
    if len(clean) > max_chars:
        raise SchemaViolation(f"{what} longer than {max_chars} characters")
    return clean
