"""Hybrid retrieval primitives: BM25, RRF fusion, filters, re-ranking."""

from __future__ import annotations

import math

import pytest

from account_intelligence.rag.models import ChunkACL, RetrievedChunk
from account_intelligence.rag.retrieval import (
    bm25_scores,
    drop_below,
    fuse_weights,
    lexical_overlap,
    metadata_filter,
    normalize,
    reciprocal_rank_fusion,
    rerank,
    select_top_k,
    tokenize,
    weighted_reciprocal_rank_fusion,
)


def _chunk(chunk_id: str, text: str, *, metadata: dict[str, str] | None = None) -> RetrievedChunk:
    """One chunk with provenance for the pure-function tests."""
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_id=f"src-{chunk_id}",
        text=text,
        score=0.5,
        metadata=metadata or {},
        acl=ChunkACL(tenant_id="t1"),
    )


class TestTokenize:
    def test_lower_cases_and_keeps_numbers(self) -> None:
        """Words are folded; numbers stay attached to their units."""
        assert tokenize("Acme 40% Revenue") == ("acme", "40%", "revenue")

    def test_drops_non_alnum_tokens(self) -> None:
        """Tokens without any alphanumeric character are dropped."""
        assert tokenize("--- ... !!!") == ()


class TestBm25:
    def test_empty_documents(self) -> None:
        """No documents means no scores."""
        assert bm25_scores("query", []) == []

    def test_exact_match_outranks_partial(self) -> None:
        """The document containing the whole query scores highest."""
        documents = ("alpha beta gamma", "alpha delta", "zzz zzz")
        scores = bm25_scores("alpha beta gamma", documents)
        assert scores[0] > scores[1] > scores[2]

    def test_normalize_scales_to_one(self) -> None:
        """The maximum becomes 1.0 and the rest scale with it."""
        assert normalize([0.0, 2.0, 4.0]) == [0.0, 0.5, 1.0]

    def test_normalize_all_zero(self) -> None:
        """An all-zero vector stays all-zero (no division by zero)."""
        assert normalize([0.0, 0.0]) == [0.0, 0.0]


class TestFusion:
    def test_rrf_ranks_first_higher(self) -> None:
        """A first-ranked document fuses higher than a second-ranked one."""
        fused = reciprocal_rank_fusion([("a", "b")], k=60)
        assert fused["a"] == pytest.approx(1 / 61)
        assert fused["a"] > fused["b"]

    def test_rrf_accumulates_across_arms(self) -> None:
        """A document ranked by both arms fuses the sum of both ranks."""
        fused = reciprocal_rank_fusion([("a",), ("a",)], k=60)
        assert fused["a"] == pytest.approx(2 / 61)

    def test_rrf_rejects_bad_k(self) -> None:
        """A non-positive fusion constant is a configuration error."""
        with pytest.raises(ValueError):
            reciprocal_rank_fusion([("a",)], k=0)

    def test_weighted_rrf_first_rank_is_the_arm_weight(self) -> None:
        """Rank one in a single arm scores exactly that arm's weight."""
        fused = weighted_reciprocal_rank_fusion({"dense": ("a",)}, {"dense": 0.7})
        assert fused["a"] == pytest.approx(0.7)

    def test_weighted_rrf_all_arms_first_is_one(self) -> None:
        """Rank one in every weighted arm scores exactly 1.0."""
        fused = weighted_reciprocal_rank_fusion(
            {"dense": ("a",), "sparse": ("a",)},
            {"dense": 0.7, "sparse": 0.3},
        )
        assert fused["a"] == pytest.approx(1.0)

    def test_weighted_rrf_negative_weight_raises(self) -> None:
        """Negative arm weights are rejected."""
        with pytest.raises(ValueError):
            weighted_reciprocal_rank_fusion({"dense": ("a",)}, {"dense": -1.0})

    def test_fuse_weights_missing_arm_scores_zero(self) -> None:
        """A document only one arm saw still fuses with the other at 0."""
        fused = fuse_weights({"a": 1.0}, {}, dense_weight=0.7, sparse_weight=0.3)
        assert fused["a"] == pytest.approx(0.7)

    def test_fuse_weights_rejects_negative(self) -> None:
        """Negative fusion weights are rejected."""
        with pytest.raises(ValueError):
            fuse_weights({}, {}, dense_weight=-0.5, sparse_weight=0.5)


class TestMetadataFilter:
    def test_required_drops_chunks_missing_the_key(self) -> None:
        """Fail closed: a chunk without a filter key is dropped."""
        chunks = (_chunk("a", "t", metadata={"tier": "premium"}), _chunk("b", "t"))
        assert metadata_filter(chunks, {"tier": "premium"}) == (chunks[0],)

    def test_required_false_treats_absent_key_as_absent(self) -> None:
        """With required=False only present keys must match."""
        chunks = (_chunk("a", "t"), _chunk("b", "t", metadata={"tier": "basic"}))
        assert metadata_filter(chunks, {"tier": "premium"}, required=False) == (chunks[0],)

    def test_mismatch_is_dropped(self) -> None:
        """A present key with the wrong value drops the chunk."""
        chunks = (_chunk("a", "t", metadata={"tier": "basic"}),)
        assert metadata_filter(chunks, {"tier": "premium"}) == ()


class TestRerank:
    def test_lexical_overlap_identical_is_one(self) -> None:
        """Identical token sets overlap completely."""
        assert lexical_overlap("acme revenue", "revenue acme") == 1.0

    def test_lexical_overlap_disjoint_is_zero(self) -> None:
        """Disjoint token sets do not overlap."""
        assert lexical_overlap("acme", "zeta") == 0.0

    def test_rerank_blend_cannot_promote_zero_score(self) -> None:
        """A zero-fused chunk stays below a strong one for any blend."""
        strong = _chunk("strong", "acme revenue growth")
        weak = _chunk("weak", "acme revenue growth")
        ordered = rerank(
            "acme revenue growth",
            (weak, strong),
            fused={"strong": 0.9, "weak": 0.0},
            lexical_weight=0.99,
        )
        assert ordered[0].chunk_id == "strong"

    def test_rerank_reorders_near_equal_candidates(self) -> None:
        """Near-equal fused scores reorder on lexical overlap."""
        exact = _chunk("exact", "acme revenue growth")
        vague = _chunk("vague", "unrelated text")
        ordered = rerank(
            "acme revenue growth",
            (vague, exact),
            fused={"exact": 0.5, "vague": 0.5},
            lexical_weight=0.5,
        )
        assert ordered[0].chunk_id == "exact"

    def test_rerank_rejects_out_of_range_weight(self) -> None:
        """The blend weight must stay within [0, 1]."""
        with pytest.raises(ValueError):
            rerank("q", (), fused={}, lexical_weight=1.5)

    def test_drop_below_threshold(self) -> None:
        """Chunks under the minimum fused score are dropped."""
        chunks = (_chunk("a", "t"), _chunk("b", "t"))
        assert drop_below(chunks, {"a": 0.05, "b": 0.5}, 0.1) == (chunks[1],)

    def test_select_top_k(self) -> None:
        """Only the first top_k chunks survive selection."""
        chunks = (_chunk("a", "t"), _chunk("b", "t"), _chunk("c", "t"))
        assert select_top_k(chunks, 2) == (chunks[0], chunks[1])

    def test_select_top_k_rejects_negative(self) -> None:
        """A negative top_k is a configuration error."""
        with pytest.raises(ValueError):
            select_top_k((), -1)

    def test_scores_are_bounded(self) -> None:
        """BM25 normalization keeps the score inside [0, 1]."""
        scores = bm25_scores("alpha", ("alpha beta", "alpha"))
        assert all(score >= 0.0 for score in normalize(scores))
        assert math.isfinite(sum(normalize(scores)))
