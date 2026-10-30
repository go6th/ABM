"""Hybrid retrieval primitives: BM25, RRF fusion, filters, re-ranking."""

from __future__ import annotations

import math

import pytest

from account_intelligence.rag.config import RetrievalConfig
from account_intelligence.rag.models import ChunkACL, RetrievedChunk
from account_intelligence.rag.retrieval import (
    FilterTooWide,
    Predicate,
    bm25_scores,
    dedupe,
    drop_below,
    filtered_set,
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
    def test_lower_cases_and_strips_punctuation(self) -> None:
        """Words are folded; punctuation at a token's edge is dropped."""
        assert tokenize("Acme 40% Revenue") == ("acme", "40", "revenue")

    def test_drops_non_alnum_tokens(self) -> None:
        """Tokens without any alphanumeric character are dropped."""
        assert tokenize("--- ... !!!") == ()

    def test_punctuation_never_glues_to_a_platform_name(self) -> None:
        """``(Salesforce,`` and ``Veeva/Salesforce`` both yield the bare platform token, so a
        keyword query for ``salesforce`` reaches a cell like ``Veeva, Salesforce``."""
        assert tokenize("(Salesforce,") == ("salesforce",)
        assert tokenize("Veeva/Salesforce") == ("veeva", "salesforce")
        assert tokenize("SFMC/Pardot") == ("sfmc", "pardot")

    def test_dates_and_versions_stay_whole(self) -> None:
        """A dot or hyphen between alphanumerics joins; at an edge it does not."""
        assert tokenize("first seen 2026-03-01.") == ("first", "seen", "2026-03-01")
        assert tokenize("release v3.2 (beta)") == ("release", "v3.2", "beta")


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

    def test_predicates_compare_dates_numbers_and_lists_by_type(self) -> None:
        """ISO dates order as dates, numbers as numbers, and ``icontains`` matches a whole
        item of a multi-valued cell before a substring."""
        rows = (
            _chunk(
                "a",
                "t",
                metadata={
                    "first_seen": "2026-03-01",
                    "status": "Closed",
                    "platforms_named": "Veeva, Salesforce",
                    "requisition": "543372",
                },
            ),
            _chunk(
                "b",
                "t",
                metadata={
                    "first_seen": "2026-02-28",
                    "status": "Open",
                    "platforms_named": "SAP",
                    "requisition": "9",
                },
            ),
        )
        after = metadata_filter(rows, [Predicate("first_seen", "gte", "2026-03-01")])
        assert [c.chunk_id for c in after] == ["a"]
        named = metadata_filter(rows, [Predicate("platforms_named", "icontains", "salesforce")])
        assert [c.chunk_id for c in named] == ["a"]
        assert metadata_filter(rows, [Predicate("platforms_named", "contains", "salesforce")]) == ()
        big = metadata_filter(rows, [Predicate("requisition", "gt", 100)])
        assert [c.chunk_id for c in big] == ["a"]  # numeric, not lexical ("9" > "543372")
        either = metadata_filter(rows, [Predicate("status", "in", ["open", "closed"])])
        assert [c.chunk_id for c in either] == ["a", "b"]
        assert metadata_filter(rows, [Predicate("status", "neq", "Open")])[0].chunk_id == "a"
        assert metadata_filter(rows, [Predicate("missing", "is_null")]) == ()  # fail closed
        assert [c.chunk_id for c in metadata_filter(rows, [Predicate("status", "not_null")])] == [
            "a",
            "b",
        ]

    def test_predicates_fail_closed_on_a_missing_column(self) -> None:
        """A chunk without the predicate's column is dropped when the filter is required."""
        rows = (_chunk("a", "t", metadata={"status": "Open"}), _chunk("b", "t"))
        assert [c.chunk_id for c in metadata_filter(rows, [Predicate("status", "eq", "Open")])] == [
            "a"
        ]
        assert metadata_filter(rows, [Predicate("status", "eq", "Open")], required=False) == rows

    def test_an_unknown_op_is_refused(self) -> None:
        """The predicate operators are the tool's closed enum."""
        with pytest.raises(ValueError, match="unknown predicate op"):
            Predicate("status", "like", "Open")

    def test_a_filtered_result_is_the_whole_set(self) -> None:
        """300 matching rows come back whole whatever the top-k is; the guard raises rather
        than truncating."""
        rows = tuple(
            _chunk(f"r{i:03d}", "t", metadata={"status": "Closed", "row": str(i + 2)})
            for i in range(300)
        )
        kept = filtered_set(rows, [Predicate("status", "eq", "Closed")])
        assert len(kept) == 300 and len(select_top_k(kept, 10)) == 10
        with pytest.raises(FilterTooWide, match="narrow the filter"):
            filtered_set(rows, [Predicate("status", "eq", "Closed")], max_filter_rows=100)


class TestDedupe:
    def test_duplicate_business_keys_collapse_to_the_latest_last_seen(self) -> None:
        """Two rows of one requisition become one hit, the one seen last."""
        rows = (
            _chunk("a", "t", metadata={"requisition": "543372", "last_seen": "2026-05-01"}),
            _chunk("b", "t", metadata={"requisition": "543372", "last_seen": "2026-06-09"}),
            _chunk("c", "t", metadata={"requisition": "1", "row": "7"}),
            _chunk("d", "t", metadata={"requisition": "1", "row": "3"}),
            _chunk("e", "t", metadata={"row": "9"}),  # no key: kept as it is
        )
        assert [c.chunk_id for c in dedupe(rows, "requisition")] == ["b", "d", "e"]


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


class TestRerank:
    def test_lexical_overlap_identical_is_one(self) -> None:
        """Identical token sets overlap completely."""
        assert lexical_overlap("acme revenue", "revenue acme") == 1.0

    def test_lexical_overlap_disjoint_is_zero(self) -> None:
        """Disjoint token sets do not overlap."""
        assert lexical_overlap("acme", "zeta") == 0.0

    def test_lexical_overlap_is_query_coverage_not_jaccard(self) -> None:
        """A long passage holding every query token scores 1.0, whatever its length."""
        long_row = " ".join(["filler"] * 300) + " salesforce open 2026-03-01"
        assert lexical_overlap("open salesforce 2026-03-01", long_row) == 1.0
        assert lexical_overlap("open salesforce veeva", long_row) == pytest.approx(2 / 3)

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
