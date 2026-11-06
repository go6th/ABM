"""Parked from tests/rag/test_retrieval.py with rag/config.py (ADR-0136)."""

from __future__ import annotations

import pytest

from account_intelligence.rag.config import RetrievalConfig


class TestConfigGuard:
    def test_a_min_score_above_the_smaller_arm_weight_fails_startup(self) -> None:
        """A gate above the sparse weight would drop every keyword-only hit."""
        with pytest.raises(ValueError, match="single-arm hit would be dropped"):
            RetrievalConfig(
                top_k=8,
                dense_weight=0.7,
                sparse_weight=0.4,
                rrf_k=60,
                lexical_weight=0.25,
                min_score=0.6,
                metadata_required=True,
            )
