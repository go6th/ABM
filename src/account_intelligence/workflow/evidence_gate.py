"""Deterministic validation between extraction and repository writes."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


@dataclass(frozen=True)
class EvidenceItem:
    source_code: str
    quote: str
    retrieved_at: datetime
    url: str | None = None


@dataclass(frozen=True)
class ProposedFact:
    claim: str
    evidence: tuple[EvidenceItem, ...]
    product_grade: str | None = None
    read_in_primary_this_run: bool = False
    confirming_question: str | None = None
    figure: str | None = None


@dataclass(frozen=True)
class GateResult:
    accepted: bool
    drop_reason: str | None = None
    product_grade: str | None = None
    evidence: tuple[EvidenceItem, ...] = ()


class EvidenceGate:
    """Gate claims against stored raw source text and source records."""

    def validate(
        self,
        fact: ProposedFact,
        raw_text: dict[str, str],
        source_urls: dict[str, str],
    ) -> GateResult:
        if not fact.evidence:
            return GateResult(False, "missing_evidence")
        if fact.product_grade == "hypothesis" and not (fact.confirming_question or "").strip():
            return GateResult(False, "hypothesis_missing_question")
        if fact.product_grade == "confirmed" and not fact.read_in_primary_this_run:
            return GateResult(False, "confirmed_without_primary_read")
        valid: list[EvidenceItem] = []
        for item in fact.evidence:
            stored = raw_text.get(item.source_code)
            if stored is None or item.source_code not in source_urls:
                return GateResult(False, "unknown_source")
            if item.url is not None:
                claimed, actual = urlparse(item.url), urlparse(source_urls[item.source_code])
                stored_host = (actual.hostname or "").casefold()
                if (
                    claimed.scheme not in {"http", "https"}
                    or not claimed.hostname
                    or claimed.hostname.casefold() != stored_host
                    or claimed.path != actual.path
                    or claimed.query != actual.query
                ):
                    return GateResult(False, "untrusted_url")
            quote = normalize_text(item.quote)
            if not quote or quote not in normalize_text(stored):
                return GateResult(False, "unverifiable_quote")
            if fact.figure and not self._figure_in_source(fact.figure, stored):
                return GateResult(False, "unverifiable_figure")
            valid.append(item)
        return GateResult(True, product_grade=fact.product_grade, evidence=tuple(valid))

    @staticmethod
    def _figure_in_source(figure: str, text: str) -> bool:
        # Exact numeric tokens plus adjacent unit tokens after Unicode/whitespace normalization.
        def tokens(value: str) -> tuple[str, ...]:
            return tuple(re.findall(r"\d+(?:[.,]\d+)?|[a-z%$€£]+", normalize_text(value)))

        claim = tokens(figure)
        body = tokens(text)
        return bool(claim) and any(
            body[index : index + len(claim)] == claim for index in range(len(body))
        )
