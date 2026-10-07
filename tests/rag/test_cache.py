"""Semantic cache: similarity gating, TTL and LRU eviction."""

from __future__ import annotations

import pytest

from account_intelligence.rag.cache import (
    SemanticCache,
    cosine_similarity,
)
from account_intelligence.rag.models import QaAnswer
from tests.rag.fakes import FakeClock


def _answer(text: str = "the answer") -> QaAnswer:
    """A non-refusal answer worth caching."""
    return QaAnswer(
        reasoning_trace="because",
        confidence_score=0.9,
        requires_more_info=False,
        final_answer=text,
    )


class TestCosineSimilarity:
    def test_identical_vectors(self) -> None:
        """A vector is perfectly similar to itself."""
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        """Perpendicular vectors are orthogonal."""
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self) -> None:
        """Opposite vectors score -1."""
        assert cosine_similarity([1.0], [-1.0]) == pytest.approx(-1.0)

    def test_zero_vector_is_orthogonal(self) -> None:
        """A zero vector has no direction."""
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_dimension_mismatch_raises(self) -> None:
        """Vectors of different lengths cannot be compared."""
        with pytest.raises(ValueError):
            cosine_similarity([1.0], [1.0, 0.0])


class TestSemanticCache:
    def test_invalid_configuration_raises(self) -> None:
        """Out-of-range knobs are configuration errors."""
        with pytest.raises(ValueError):
            SemanticCache(similarity_threshold=1.5, ttl_seconds=1, max_entries=1)
        with pytest.raises(ValueError):
            SemanticCache(similarity_threshold=0.5, ttl_seconds=-1, max_entries=1)
        with pytest.raises(ValueError):
            SemanticCache(similarity_threshold=0.5, ttl_seconds=1, max_entries=0)

    def test_store_and_lookup_hit(self) -> None:
        """An identical query clears the strict similarity gate."""
        cache = SemanticCache(similarity_threshold=0.92, ttl_seconds=3600, max_entries=10)
        cache.store([1.0, 0.0], _answer(), now=0.0)
        assert cache.lookup([1.0, 0.0], now=1.0) == _answer()

    def test_below_threshold_is_a_miss(self) -> None:
        """A different query must not be served a cached answer."""
        cache = SemanticCache(similarity_threshold=0.92, ttl_seconds=3600, max_entries=10)
        cache.store([1.0, 0.0], _answer(), now=0.0)
        assert cache.lookup([0.0, 1.0], now=1.0) is None

    def test_stale_entry_is_a_miss(self) -> None:
        """An entry past its TTL is never served."""
        cache = SemanticCache(similarity_threshold=0.92, ttl_seconds=60, max_entries=10)
        cache.store([1.0, 0.0], _answer(), now=0.0)
        assert cache.lookup([1.0, 0.0], now=61.0) is None

    def test_lru_eviction(self) -> None:
        """The least recently used entry goes first."""
        cache = SemanticCache(similarity_threshold=0.9, ttl_seconds=3600, max_entries=2)
        cache.store([1.0, 0.0, 0.0], _answer("one"), now=0.0)
        cache.store([0.0, 1.0, 0.0], _answer("two"), now=1.0)
        cache.store([0.0, 0.0, 1.0], _answer("three"), now=2.0)
        assert cache.lookup([1.0, 0.0, 0.0], now=3.0) is None
        assert cache.lookup([0.0, 1.0, 0.0], now=3.0) == _answer("two")

    def test_lookup_refreshes_recency(self) -> None:
        """A hit protects the entry from the next eviction."""
        cache = SemanticCache(similarity_threshold=0.9, ttl_seconds=3600, max_entries=2)
        cache.store([1.0, 0.0, 0.0], _answer("one"), now=0.0)
        cache.store([0.0, 1.0, 0.0], _answer("two"), now=1.0)
        assert cache.lookup([1.0, 0.0, 0.0], now=2.0) == _answer("one")
        cache.store([0.0, 0.0, 1.0], _answer("three"), now=3.0)
        assert cache.lookup([0.0, 1.0, 0.0], now=4.0) is None
        assert cache.lookup([1.0, 0.0, 0.0], now=4.0) == _answer("one")

    def test_invalidate(self) -> None:
        """Invalidation drops the exact-matching entry."""
        cache = SemanticCache(similarity_threshold=0.9, ttl_seconds=3600, max_entries=10)
        cache.store([1.0, 0.0], _answer(), now=0.0)
        assert cache.invalidate([1.0, 0.0]) is True
        assert cache.invalidate([1.0, 0.0]) is False
        assert cache.lookup([1.0, 0.0], now=1.0) is None

    def test_clock_drives_ttl(self) -> None:
        """The injected clock decides what is stale."""
        clock = FakeClock()
        cache = SemanticCache(similarity_threshold=0.9, ttl_seconds=60, max_entries=10)
        cache.store([1.0, 0.0], _answer(), now=clock.now().timestamp())
        clock.advance(30.0)
        assert cache.lookup([1.0, 0.0], now=clock.now().timestamp())
        clock.advance(31.0)
        assert cache.lookup([1.0, 0.0], now=clock.now().timestamp()) is None
