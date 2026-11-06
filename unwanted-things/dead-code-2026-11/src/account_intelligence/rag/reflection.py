"""Self-correction: critiquing retrieved context before generation.

The reflection node is the pipeline's defect filter. After the
initial retrieval, and BEFORE the generation call, a distinct
evaluation critiques the context for relevance, completeness and
quality. Weak context is never papered over by the generator:
the query is refined and retrieval runs again, up to the
configured pass cap.

The critique is deterministic here (lexical coverage of the
query's terms in the context, chunk quality from retrieval
scores) with an optional ``JudgePort`` — a distinct model tier
— replacing the lexical estimate when configured.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from account_intelligence.rag.config import ReflectionConfig
from account_intelligence.rag.models import (
    ReflectionVerdict,
    RetrievedChunk,
)
from account_intelligence.rag.tokenizer import tokenize

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "if",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "with",
    }
)


def content_terms(text: str) -> tuple[str, ...]:
    """The query's content tokens: stopwords and duplicates removed."""
    return tuple(sorted({term for term in tokenize(text) if term not in _STOPWORDS}))


def coverage(query: str, chunks: Sequence[RetrievedChunk]) -> float:
    """Fraction of query content terms present in the retrieved context.

    The deterministic completeness estimate: terms the user asked
    about that appear nowhere in the context are the gaps the
    reflection loop refines the query with.
    """
    terms = content_terms(query)
    if not terms:
        return 1.0
    context: set[str] = set()
    for chunk in chunks:
        context.update(tokenize(chunk.text))
    present = sum(1 for term in terms if term in context)
    return present / len(terms)


def context_quality(chunks: Sequence[RetrievedChunk]) -> float:
    """Mean retrieval score of the context (the quality axis)."""
    if not chunks:
        return 0.0
    return sum(chunk.score for chunk in chunks) / len(chunks)


def relevance(query: str, chunks: Sequence[RetrievedChunk]) -> float:
    """Best lexical overlap of the query with any single chunk."""
    from account_intelligence.rag.retrieval import lexical_overlap

    if not chunks:
        return 0.0
    return max(lexical_overlap(query, chunk.text) for chunk in chunks)


class JudgePort(Protocol):
    """The reflection model: a distinct tier from the generator."""

    async def critique(
        self, query: str, chunks: Sequence[RetrievedChunk]
    ) -> tuple[float, float, float]:
        """Return (relevance, completeness, quality) in [0, 1]."""
        ...


def evaluate(
    query: str,
    chunks: Sequence[RetrievedChunk],
    config: ReflectionConfig,
    *,
    judge: JudgePort | None = None,
    judged: tuple[float, float, float] | None = None,
) -> ReflectionVerdict:
    """Critique the context and decide whether it is sufficient.

    ``judged`` carries the judge model's scores when the caller
    already ran it (the async node awaits the port, then calls
    this synchronously); without a judge the deterministic
    estimates stand. Sufficient means every axis clears its
    threshold AND the context is non-empty.
    """
    if judged is not None:
        relevance_score, completeness_score, quality_score = judged
    else:
        relevance_score = relevance(query, chunks)
        completeness_score = coverage(query, chunks)
        quality_score = context_quality(chunks)
    sufficient = bool(
        chunks
        and relevance_score >= config.relevance_threshold
        and completeness_score >= config.completeness_threshold
        and quality_score >= config.quality_threshold
    )
    terms = content_terms(query)
    context: set[str] = set()
    for chunk in chunks:
        context.update(tokenize(chunk.text))
    gaps = tuple(term for term in terms if term not in context)
    refined = refine_query(query, gaps) if gaps else None
    return ReflectionVerdict(
        sufficient=sufficient,
        relevance=round(relevance_score, 6),
        completeness=round(completeness_score, 6),
        quality=round(quality_score, 6),
        gaps=gaps,
        refined_query=refined,
    )


def refine_query(query: str, gaps: Sequence[str]) -> str | None:
    """Fold the uncovered terms back into the query.

    Refinement is the loop's search strategy: the next
    retrieval pass is seeded with the terms the last pass
    missed, phrased as a narrower follow-up.
    """
    if not gaps:
        return None
    return f"{query} ({', '.join(gaps)})"


def decide(
    verdict: ReflectionVerdict,
    *,
    passes: int,
    config: ReflectionConfig,
) -> str:
    """The reflection node's routing decision.

    ``retry`` loops back to retrieval with the refined query;
    ``generate`` proceeds to the generator; ``refuse`` ends the
    loop with the refusal payload. The pass cap bounds the loop
    even when refinement keeps producing new queries.
    """
    if verdict.sufficient:
        return "generate"
    if passes < config.max_passes and verdict.refined_query is not None:
        return "retry"
    return "refuse"
