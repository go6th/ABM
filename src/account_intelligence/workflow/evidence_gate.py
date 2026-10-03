"""Deterministic validation between extraction and repository writes.

The quote, URL and figure checks delegate to ``account_intelligence.grading.spans`` (one
implementation per rule). The authoritative path is ``grading.pipeline.grade_claims``, which
also recomputes ``read_in_primary_this_run`` in code; this gate's caller-supplied flag is a
pre-check only (G5: no caller parameter can set the real flag).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from account_intelligence.grading.spans import casefold_text, figure_in_text, normalise_ws


def normalize_text(value: str) -> str:
    """Case-folded comparison form (kept for callers); quotes are checked verbatim."""
    return casefold_text(value)


@dataclass(frozen=True)
class EvidenceItem:
    """One cited source item: source code, quote, retrieval time, optional URL."""

    source_code: str
    quote: str
    retrieved_at: datetime
    url: str | None = None


@dataclass(frozen=True)
class ProposedFact:
    """A fact a stage proposes, with its evidence (pre-check before grading)."""

    claim: str
    evidence: tuple[EvidenceItem, ...]
    product_grade: str | None = None
    read_in_primary_this_run: bool = False
    confirming_question: str | None = None
    figure: str | None = None


@dataclass(frozen=True)
class GateResult:
    """Whether the fact passed, and the drop reason when not."""

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
        """Evidence-or-drop, verbatim quote, URL and figure checks (delegating to grading.spans)."""
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
            quote = normalise_ws(item.quote)
            if not quote or quote not in normalise_ws(stored):
                return GateResult(False, "unverifiable_quote")
            if fact.figure and not self._figure_in_source(fact.figure, stored):
                return GateResult(False, "unverifiable_figure")
            valid.append(item)
        return GateResult(True, product_grade=fact.product_grade, evidence=tuple(valid))

    @staticmethod
    def _figure_in_source(figure: str, text: str) -> bool:
        return figure_in_text(figure, text)
