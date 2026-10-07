"""The RAG Triad and the information-retrieval metrics.

Quality is decomposed into independently measurable axes so
a regression localizes: retrieval failures (low precision,
low recall, bad ranking) are diagnosed BEFORE generation,
and generation failures (ungroundedness, irrelevance) after.

Everything here is a pure function of its arguments — CI can
gate on these numbers without a model, a database or a run.
The LLM-judged variants (context relevance, faithfulness)
take the judge's binary labels as inputs; the formulas are
the blueprint's, including the logarithmic NDCG discount.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def precision_at_k(
    retrieved: Sequence[str], relevant: frozenset[str] | set[str], *, k: int
) -> float:
    """Fraction of the top ``k`` retrieved docs that are relevant."""
    if k <= 0:
        raise ValueError("k must be positive")
    top = retrieved[:k]
    if not top:
        return 0.0
    hits = sum(1 for doc_id in top if doc_id in relevant)
    return hits / len(top)


def recall_at_k(retrieved: Sequence[str], relevant: frozenset[str] | set[str], *, k: int) -> float:
    """Fraction of all relevant docs found in the top ``k``."""
    if k <= 0:
        raise ValueError("k must be positive")
    if not relevant:
        return 0.0
    top = set(retrieved[:k])
    return len(top & relevant) / len(relevant)


def reciprocal_rank(ranked: Sequence[str], relevant: frozenset[str] | set[str]) -> float:
    """1 / rank of the first relevant doc; 0 when none is found."""
    for rank, doc_id in enumerate(ranked, start=1):
        if doc_id in relevant:
            return 1.0 / rank
    return 0.0


def mrr(queries: Sequence[tuple[Sequence[str], frozenset[str] | set[str]]]) -> float:
    """Mean reciprocal rank across queries."""
    if not queries:
        return 0.0
    return sum(reciprocal_rank(ranked, relevant) for ranked, relevant in queries) / len(queries)


def dcg(relevances: Sequence[float], *, k: int) -> float:
    """Discounted cumulative gain with the log2(i+1) discount."""
    if k <= 0:
        raise ValueError("k must be positive")
    total = 0.0
    for index, relevance in enumerate(relevances[:k]):
        total += (2.0**relevance - 1.0) / math.log2(index + 2)
    return total


def ndcg(relevances: Sequence[float], *, k: int) -> float:
    """NDCG@k: DCG over the ideal ranking's DCG."""
    ideal = sorted(relevances, reverse=True)
    ideal_dcg = dcg(ideal, k=k)
    if ideal_dcg == 0.0:
        return 0.0
    return dcg(relevances, k=k) / ideal_dcg


def groundedness(
    statements: Sequence[bool],
) -> float:
    """Groundedness (faithfulness): supported statements over all.

    The input is the judge's per-statement label: True when the
    statement is logically deducible from the retrieved context.
    A generation that asserts facts the context cannot support
    scores below 1.0 no matter how fluent it reads.
    """
    if not statements:
        return 1.0
    return sum(1 for supported in statements if supported) / len(statements)


def conversational_faithfulness(
    information_statements: Sequence[bool],
) -> float:
    """Conversational faithfulness: grounded information statements.

    Only information-containing sentences are counted (chit-chat
    is neither faithful nor unfaithful); ungrounded information
    statements drag the score toward 0.
    """
    if not information_statements:
        return 1.0
    grounded = sum(1 for grounded_stmt in information_statements if grounded_stmt)
    return grounded / len(information_statements)


def answer_relevance(query: str, answer: str, *, content_only: bool = True) -> float:
    """Lexical alignment of the answer with the query.

    A deterministic proxy for the judged metric: the fraction
    of the query's content terms the answer addresses. The
    LLM judge replaces this in production; the proxy exists
    so the gate has a floor that needs no model.
    """
    from account_intelligence.rag.reflection import content_terms

    terms = content_terms(query)
    if not terms:
        return 1.0
    answer_terms = set(term for term in answer.casefold().split() if term.isalnum())
    addressed = sum(1 for term in terms if term in answer_terms)
    return addressed / len(terms)


def refusal_accuracy(
    cases: Sequence[tuple[bool, bool]],
) -> float:
    """Refusal accuracy: (expected, actual) refusal labels.

    True means "this query should be refused". A system that
    guesses outside its scope scores 0.0; one that declines
    exactly when it should scores 1.0.
    """
    if not cases:
        return 1.0
    correct = sum(1 for expected, actual in cases if expected == actual)
    return correct / len(cases)
