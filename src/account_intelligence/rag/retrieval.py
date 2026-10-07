"""Hybrid retrieval: BM25 scoring, rank fusion and deterministic re-ranking.

Pure arithmetic only — the actual vector and keyword lookups are ports
(``DenseRetrieverPort``, ``SparseRetrieverPort``); this module owns the
fusion logic: reciprocal rank fusion of the two arms, metadata
predicates applied BEFORE ranking (fail closed: a chunk missing a
required filter key is dropped, not passed through), and a deterministic
lexical re-ranker that blends with the fused score so ranking quality
does not depend on a served model at test time.

The cross-encoder re-ranker of the blueprint is the optional
``RerankerPort``; when it is configured its scores replace the lexical
blend, and when it is not, the lexical blend runs instead (never a
silent no-op).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol

from account_intelligence.rag.models import RetrievedChunk


def tokenize(text: str) -> tuple[str, ...]:
    """Lower-cased word tokens; numbers stay attached to their units."""
    return tuple(
        token.casefold() for token in text.split() if any(char.isalnum() for char in token)
    )


def bm25_scores(
    query: str,
    documents: Sequence[str],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    """BM25 scores of ``documents`` against ``query`` (the sparse arm).

    Scores are raw BM25 (unbounded); callers that fuse them with cosine
    scores in [0, 1] must normalise first — ``normalize`` does exactly
    that with a deterministic zero-safe divide.
    """
    tokenized = [tokenize(document) for document in documents]
    total = len(documents)
    if total == 0:
        return []
    containing: Counter[str] = Counter()
    for terms in tokenized:
        containing.update(set(terms))
    avgdl = max((sum(len(terms) for terms in tokenized) / total), 1.0)
    query_terms = set(tokenize(query))
    scores: list[float] = []
    for terms in tokenized:
        freq = Counter(terms)
        doc_len = max(len(terms), 1)
        score = 0.0
        for term in query_terms:
            if term not in containing:
                continue
            idf = math.log(1.0 + (total - containing[term] + 0.5) / (containing[term] + 0.5))
            tf = freq.get(term, 0)
            if tf:
                score += idf * (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * doc_len / avgdl))
        scores.append(score)
    return scores


def normalize(scores: Sequence[float]) -> list[float]:
    """Scale scores to [0, 1] by the maximum; all-zero stays all-zero."""
    peak = max(scores, default=0.0)
    if peak <= 0.0:
        return [0.0] * len(scores)
    return [score / peak for score in scores]


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], *, k: int = 60) -> dict[str, float]:
    """Reciprocal rank fusion: one fused score per document id.

    ``1 / (k + rank)`` per arm; ties break on the id so the ordering is
    deterministic across runs and tenants (a fused order that flips
    between identical inputs is a production incident).
    """
    if k < 1:
        raise ValueError("rrf k must be positive")
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return fused


def weighted_reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[str]],
    weights: Mapping[str, float],
    *,
    k: int = 60,
) -> dict[str, float]:
    """Weighted reciprocal rank fusion, normalised to [0, 1].

    Each arm contributes ``weight / (k + rank + 1)`` per document —
    the hybrid fusion step, with the configured arm weights shaping
    how much the semantic and keyword arms each matter (rank fusion
    is scale-free, so heterogeneous arm scores fuse safely). The sum
    is scaled by ``k + 1`` so a document ranked first by a single
    arm scores exactly that arm's weight, and one ranked first by
    every weighted arm scores 1.0: the fused score stays within
    [0, 1] and the ``min_score`` gate keeps its meaning.
    """
    if k < 1:
        raise ValueError("rrf k must be positive")
    fused: dict[str, float] = {}
    for arm, ranking in rankings.items():
        weight = weights.get(arm, 0.0)
        if weight < 0.0:
            raise ValueError("arm weights cannot be negative")
        for rank, doc_id in enumerate(ranking):
            fused[doc_id] = fused.get(doc_id, 0.0) + weight / (k + rank + 1)
    scale = float(k + 1)
    return {doc_id: min(1.0, score * scale) for doc_id, score in fused.items()}


def fuse_weights(
    dense: Mapping[str, float],
    sparse: Mapping[str, float],
    *,
    dense_weight: float,
    sparse_weight: float,
) -> dict[str, float]:
    """Weighted fusion of per-document arm scores (missing arm scores 0)."""
    if dense_weight < 0.0 or sparse_weight < 0.0:
        raise ValueError("fusion weights cannot be negative")
    fused: dict[str, float] = {}
    for doc_id in {*dense, *sparse}:
        fused[doc_id] = dense_weight * dense.get(doc_id, 0.0) + sparse_weight * sparse.get(
            doc_id, 0.0
        )
    return fused


def metadata_filter(
    chunks: Iterable[RetrievedChunk],
    filters: Mapping[str, str],
    *,
    required: bool = True,
) -> tuple[RetrievedChunk, ...]:
    """Keep only chunks whose metadata matches every filter key.

    Fail closed (the blueprint's metadata-filtering rule): with
    ``required=True`` a chunk that does not carry a filter key is dropped
    rather than assumed to match; with ``required=False`` the key is
    treated as absent and only present keys must match.
    """
    kept: list[RetrievedChunk] = []
    for chunk in chunks:
        matches = True
        for key, wanted in filters.items():
            if key not in chunk.metadata:
                if required:
                    matches = False
                    break
                continue
            if chunk.metadata[key] != wanted:
                matches = False
                break
        if matches:
            kept.append(chunk)
    return tuple(kept)


def lexical_overlap(query: str, text: str) -> float:
    """Jaccard overlap of query and text tokens (the deterministic re-ranker)."""
    query_terms = set(tokenize(query))
    text_terms = set(tokenize(text))
    union = query_terms | text_terms
    if not union:
        return 0.0
    return len(query_terms & text_terms) / len(union)


def rerank(
    query: str,
    chunks: Sequence[RetrievedChunk],
    *,
    fused: Mapping[str, float],
    lexical_weight: float,
) -> tuple[RetrievedChunk, ...]:
    """Blend the fused score with lexical overlap and order by the blend.

    The blend is ``1 - lexical_weight`` fused plus ``lexical_weight``
    overlap, so the re-ranker can only re-order near-equal candidates
    (it cannot promote a zero-score chunk above a strong one).
    """
    if not 0.0 <= lexical_weight <= 1.0:
        raise ValueError("lexical_weight must be within [0, 1]")

    def blend(chunk: RetrievedChunk) -> float:
        return (1.0 - lexical_weight) * fused.get(chunk.chunk_id, 0.0) + lexical_weight * (
            lexical_overlap(query, chunk.text)
        )

    return tuple(sorted(chunks, key=lambda c: (-blend(c), c.chunk_id)))


def drop_below(
    chunks: Sequence[RetrievedChunk], fused: Mapping[str, float], min_score: float
) -> tuple[RetrievedChunk, ...]:
    """Drop chunks whose fused score is below ``min_score``."""
    return tuple(c for c in chunks if fused.get(c.chunk_id, 0.0) >= min_score)


def select_top_k(chunks: Sequence[RetrievedChunk], top_k: int) -> tuple[RetrievedChunk, ...]:
    """The first ``top_k`` chunks (callers pass an ordered sequence)."""
    if top_k < 0:
        raise ValueError("top_k cannot be negative")
    return tuple(chunks[:top_k])


class DenseRetrieverPort(Protocol):
    """The semantic arm: nearest chunks for a query embedding."""

    async def search(
        self, embedding: Sequence[float], top_k: int, filters: Mapping[str, Any]
    ) -> list[RetrievedChunk]:
        """Return the nearest chunks, highest score first."""
        ...


class SparseRetrieverPort(Protocol):
    """The keyword arm: BM25 hits for the raw query."""

    async def search(
        self, query: str, top_k: int, filters: Mapping[str, Any]
    ) -> list[RetrievedChunk]:
        """Return the matching chunks, highest score first."""
        ...


class RerankerPort(Protocol):
    """The optional cross-encoder arm (the blueprint's re-ranking step)."""

    async def rerank(self, query: str, chunks: Sequence[RetrievedChunk]) -> list[RetrievedChunk]:
        """Re-order chunks by cross-encoder relevance, best first."""
        ...
