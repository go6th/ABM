"""Reflection: context critique, query refinement and routing."""

from __future__ import annotations

import pytest

from account_intelligence.rag.config import ReflectionConfig
from account_intelligence.rag.models import RetrievedChunk
from account_intelligence.rag.reflection import (
    content_terms,
    context_quality,
    coverage,
    decide,
    evaluate,
    refine_query,
    relevance,
)

CONFIG = ReflectionConfig(
    relevance_threshold=0.6,
    completeness_threshold=0.7,
    quality_threshold=0.5,
    max_passes=3,
)


def _chunk(chunk_id: str, text: str, score: float = 0.9) -> RetrievedChunk:
    """One retrieved chunk with a retrieval-time score."""
    return RetrievedChunk(chunk_id=chunk_id, source_id=f"src-{chunk_id}", text=text, score=score)


class TestTerms:
    def test_content_terms_drop_stopwords(self) -> None:
        """Stopwords and duplicates are removed; the rest sort."""
        assert content_terms("the acme and acme revenue") == ("acme", "revenue")

    def test_content_terms_empty(self) -> None:
        """A query of only stopwords has no content terms."""
        assert content_terms("the and of") == ()


class TestEstimates:
    def test_coverage_full(self) -> None:
        """Every content term present in the context."""
        chunks = (_chunk("a", "acme revenue grew"),)
        assert coverage("acme revenue", chunks) == 1.0

    def test_coverage_partial(self) -> None:
        """Missing terms lower the completeness estimate."""
        chunks = (_chunk("a", "acme grew"),)
        assert coverage("acme revenue", chunks) == 0.5

    def test_coverage_empty_terms(self) -> None:
        """A stopword-only query is trivially covered."""
        assert coverage("the", ()) == 1.0

    def test_context_quality(self) -> None:
        """Quality is the mean retrieval score."""
        chunks = (_chunk("a", "t", 0.8), _chunk("b", "t", 0.6))
        assert context_quality(chunks) == pytest.approx(0.7)

    def test_context_quality_empty(self) -> None:
        """No context means no quality."""
        assert context_quality(()) == 0.0

    def test_relevance_best_chunk(self) -> None:
        """Relevance is the best single-chunk overlap."""
        chunks = (_chunk("a", "totally unrelated"), _chunk("b", "acme revenue"))
        assert relevance("acme revenue", chunks) == 1.0

    def test_relevance_empty(self) -> None:
        """No context means no relevance."""
        assert relevance("acme", ()) == 0.0


class TestEvaluate:
    def test_sufficient_context(self) -> None:
        """A context that clears every axis is sufficient."""
        chunks = (_chunk("a", "acme annual revenue growth report"),)
        verdict = evaluate("acme annual revenue growth", chunks, CONFIG)
        assert verdict.sufficient
        assert verdict.gaps == ()
        assert verdict.refined_query is None

    def test_insufficient_with_gaps(self) -> None:
        """Missing terms are reported and folded into a refinement."""
        chunks = (_chunk("a", "acme revenue"),)
        verdict = evaluate("acme annual revenue growth", chunks, CONFIG)
        assert not verdict.sufficient
        assert "annual" in verdict.gaps
        assert "growth" in verdict.gaps
        assert verdict.refined_query is not None
        assert "annual" in verdict.refined_query

    def test_empty_context_is_never_sufficient(self) -> None:
        """Scores alone cannot make an empty context sufficient."""
        verdict = evaluate("acme", (), CONFIG, judged=(1.0, 1.0, 1.0))
        assert not verdict.sufficient

    def test_judged_scores_replace_the_estimates(self) -> None:
        """The judge model's scores stand when provided."""
        chunks = (_chunk("a", "anything at all"),)
        verdict = evaluate("acme revenue", chunks, CONFIG, judged=(0.9, 0.9, 0.9))
        assert verdict.sufficient
        assert verdict.relevance == 0.9

    def test_judge_below_threshold_is_insufficient(self) -> None:
        """A judge score under any axis fails the context."""
        chunks = (_chunk("a", "anything at all"),)
        verdict = evaluate("acme revenue", chunks, CONFIG, judged=(0.9, 0.1, 0.9))
        assert not verdict.sufficient


class TestDecide:
    def test_sufficient_generates(self) -> None:
        """A sufficient verdict routes to the generator."""
        from account_intelligence.rag.models import ReflectionVerdict

        verdict = ReflectionVerdict(sufficient=True, relevance=1.0, completeness=1.0, quality=1.0)
        assert decide(verdict, passes=0, config=CONFIG) == "generate"

    def test_insufficient_retries_while_passes_remain(self) -> None:
        """A refinement with passes left loops back to retrieval."""
        from account_intelligence.rag.models import ReflectionVerdict

        verdict = ReflectionVerdict(
            sufficient=False,
            relevance=0.1,
            completeness=0.0,
            quality=0.0,
            refined_query="acme (growth)",
        )
        assert decide(verdict, passes=2, config=CONFIG) == "retry"

    def test_pass_cap_refuses(self) -> None:
        """Out of passes, the loop refuses instead of spinning."""
        from account_intelligence.rag.models import ReflectionVerdict

        verdict = ReflectionVerdict(
            sufficient=False,
            relevance=0.1,
            completeness=0.0,
            quality=0.0,
            refined_query="acme (growth)",
        )
        assert decide(verdict, passes=3, config=CONFIG) == "refuse"

    def test_no_refinement_refuses(self) -> None:
        """An insufficient verdict with no gaps has nothing to retry."""
        from account_intelligence.rag.models import ReflectionVerdict

        verdict = ReflectionVerdict(sufficient=False, relevance=0.1, completeness=0.0, quality=0.0)
        assert decide(verdict, passes=0, config=CONFIG) == "refuse"


class TestRefine:
    def test_refine_folds_gaps(self) -> None:
        """Gaps are appended as a narrower follow-up."""
        assert refine_query("acme revenue", ("growth",)) == "acme revenue (growth)"

    def test_refine_without_gaps(self) -> None:
        """No gaps means nothing to refine."""
        assert refine_query("acme", ()) is None
