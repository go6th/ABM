"""RAG Triad and information-retrieval metrics."""

from __future__ import annotations

import math

import pytest

from account_intelligence.rag.metrics import (
    answer_relevance,
    conversational_faithfulness,
    dcg,
    groundedness,
    mrr,
    ndcg,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    refusal_accuracy,
)

RELEVANT = frozenset({"a", "b", "c"})


class TestRetrievalMetrics:
    def test_precision_at_k(self) -> None:
        """Two of the top three retrieved are relevant."""
        assert precision_at_k(("a", "b", "x", "c"), RELEVANT, k=3) == pytest.approx(2 / 3)

    def test_precision_at_k_empty(self) -> None:
        """No retrieved documents means zero precision."""
        assert precision_at_k((), RELEVANT, k=3) == 0.0

    def test_precision_at_k_bad_k(self) -> None:
        """A non-positive k is a caller error."""
        with pytest.raises(ValueError):
            precision_at_k(("a",), RELEVANT, k=0)

    def test_recall_at_k(self) -> None:
        """Two of the three relevant documents are found."""
        assert recall_at_k(("a", "b", "x"), RELEVANT, k=3) == pytest.approx(2 / 3)

    def test_recall_at_k_no_relevant(self) -> None:
        """No relevant documents means zero recall."""
        assert recall_at_k(("a",), frozenset(), k=3) == 0.0

    def test_reciprocal_rank(self) -> None:
        """The first relevant document is at rank 2."""
        assert reciprocal_rank(("x", "b", "a"), RELEVANT) == 0.5

    def test_reciprocal_rank_none(self) -> None:
        """No relevant document means zero MRR contribution."""
        assert reciprocal_rank(("x", "y"), RELEVANT) == 0.0

    def test_mrr(self) -> None:
        """Mean reciprocal rank across queries."""
        queries = (("x", "a"), ("a",), ("x", "y"))
        assert mrr(queries) == pytest.approx((0.5 + 1.0 + 0.0) / 3)

    def test_mrr_empty(self) -> None:
        """No queries means zero MRR."""
        assert mrr(()) == 0.0

    def test_dcg_discounts_later_ranks(self) -> None:
        """The log2 discount lowers later gains."""
        assert dcg((1.0, 1.0), k=2) == pytest.approx(1.0 + 1.0 / math.log2(3))

    def test_ndcg_perfect_ranking(self) -> None:
        """The ideal ranking scores 1.0."""
        assert ndcg((3.0, 1.0), k=2) == 1.0

    def test_ndcg_imperfect_ranking(self) -> None:
        """A misordered ranking scores below 1.0."""
        assert ndcg((1.0, 3.0), k=2) == pytest.approx(
            (1.0 + (2**3 - 1) / math.log2(3)) / (7.0 + 1.0 / math.log2(3))
        )

    def test_ndcg_zero_gain(self) -> None:
        """All-zero relevances score zero."""
        assert ndcg((0.0, 0.0), k=2) == 0.0

    def test_dcg_bad_k(self) -> None:
        """A non-positive k is a caller error."""
        with pytest.raises(ValueError):
            dcg((1.0,), k=0)


class TestTriad:
    def test_groundedness(self) -> None:
        """Three of four statements are supported."""
        assert groundedness((True, True, True, False)) == 0.75

    def test_groundedness_empty(self) -> None:
        """No statements means nothing to ground."""
        assert groundedness(()) == 1.0

    def test_conversational_faithfulness(self) -> None:
        """Only information statements count."""
        assert conversational_faithfulness((True, False, True)) == pytest.approx(2 / 3)

    def test_answer_relevance(self) -> None:
        """The answer addresses the query's content terms."""
        assert answer_relevance("acme revenue growth", "acme revenue") == 0.5

    def test_answer_relevance_empty_query(self) -> None:
        """A stopword-only query is trivially addressed."""
        assert answer_relevance("the", "anything") == 1.0

    def test_refusal_accuracy(self) -> None:
        """Three of four refusal decisions are correct."""
        cases = ((True, True), (False, False), (True, False), (False, True))
        assert refusal_accuracy(cases) == 0.5

    def test_refusal_accuracy_empty(self) -> None:
        """No cases means a perfect score by definition."""
        assert refusal_accuracy(()) == 1.0
