"""Hybrid retrieval: BM25 scoring, rank fusion and deterministic re-ranking.

Pure arithmetic only — the actual vector and keyword lookups are ports
(``DenseRetrieverPort``, ``SparseRetrieverPort``); this module owns the
fusion logic: reciprocal rank fusion of the two arms, metadata
predicates applied BEFORE ranking (fail closed: a chunk missing a
required filter key is dropped, not passed through), and a deterministic
lexical re-ranker that blends with the fused score so ranking quality
does not depend on a served model at test time.

Tabular questions (2026-10-17): a filter over rows is a set, not a
sample. ``Predicate`` carries the typed operators of the ``query_table``
tool (dates compare as dates, numbers as numbers, ``contains`` splits a
multi-valued cell), ``filtered_set`` returns every match or raises past
``MAX_FILTER_ROWS``, and ``dedupe`` collapses duplicate rows on a
business key. Tables themselves are queried in ``documents.tables``;
these primitives serve the chunk path.

The cross-encoder re-ranker of the blueprint is the optional
``RerankerPort``; when it is configured its scores replace the lexical
blend, and when it is not, the lexical blend runs instead (never a
silent no-op).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Protocol

from account_intelligence.rag.models import RetrievedChunk
from account_intelligence.rag.tokenizer import tokenize

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?$")
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
_LIST_SEP = re.compile(r"\s*[,;|]\s*")
# the tabular predicate operators (the same enum as the ``query_table`` tool)
Op = Literal[
    "eq", "neq", "gt", "gte", "lt", "lte", "contains", "icontains", "in", "is_null", "not_null"
]
OPS: tuple[str, ...] = (
    "eq", "neq", "gt", "gte", "lt", "lte", "contains", "icontains", "in", "is_null", "not_null"
)  # fmt: skip
# a filtered result is a set, never a sample; past this many rows the caller must narrow it
MAX_FILTER_ROWS = 5_000


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


@dataclass(frozen=True)
class Predicate:
    """One typed filter over a chunk's metadata: ``column op value``."""

    column: str
    op: str
    value: Any = None

    def __post_init__(self) -> None:
        if self.op not in OPS:
            raise ValueError(f"unknown predicate op {self.op!r}; one of {', '.join(OPS)}")
        if self.op == "in" and not isinstance(self.value, list | tuple | set | frozenset):
            raise ValueError("the 'in' predicate takes a list of values")


class FilterTooWide(ValueError):
    """More rows match the predicates than a filtered set may hold: narrow the filter."""

    def __init__(self, matched: int, limit: int) -> None:
        super().__init__(
            f"{matched} rows match the predicates; a filtered set holds at most {limit} rows,"
            " narrow the filter"
        )
        self.matched, self.limit = matched, limit


def typed(value: Any) -> Any:
    """A cell as the value it compares as: an ISO date string as a date, a number (or a
    numeric string) as a float, anything else as text; None stays None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, date):
        return value
    text = str(value).strip()
    m = _ISO_DATE.match(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return text
    if _NUMBER.match(text):
        return float(text)
    return text


def _ordered(left: Any, right: Any) -> tuple[Any, Any] | None:
    """Both values typed alike for an ordering compare, or None when they cannot compare."""
    a, b = typed(left), typed(right)
    if a is None or b is None:
        return None
    if isinstance(a, date) and isinstance(b, date):
        return a, b
    if isinstance(a, float) and isinstance(b, float):
        return a, b
    if isinstance(a, date) or isinstance(b, date):
        return None  # a date against text never orders
    return str(a), str(b)


def _equal(left: Any, right: Any) -> bool:
    pair = _ordered(left, right)
    if pair is None:
        return left is None and right is None
    a, b = pair
    return bool(a == b) if not isinstance(a, str) else a.casefold() == str(b).casefold()


def _items(cell: Any) -> list[str]:
    """A multi-valued cell split on commas, semicolons and bars."""
    return [part for part in _LIST_SEP.split(str(cell)) if part]


def _contains(cell: Any, wanted: Any, *, fold: bool) -> bool:
    """A whole item of the cell equals ``wanted`` first, a substring of it second."""
    if cell is None or wanted is None:
        return False
    hay, needle = str(cell), str(wanted)
    if fold:
        hay, needle = hay.casefold(), needle.casefold()
    if not needle:
        return False
    return any(item == needle for item in _items(hay)) or needle in hay


def matches(predicate: Predicate, cell: Any) -> bool:
    """Whether one cell satisfies the predicate (a missing cell is passed as None)."""
    op, wanted = predicate.op, predicate.value
    if op == "is_null":
        return cell is None or str(cell).strip() == ""
    if op == "not_null":
        return not (cell is None or str(cell).strip() == "")
    if op == "eq":
        return _equal(cell, wanted)
    if op == "neq":
        return not _equal(cell, wanted)
    if op == "in":
        return any(_equal(cell, option) for option in wanted)
    if op == "contains":
        return _contains(cell, wanted, fold=False)
    if op == "icontains":
        return _contains(cell, wanted, fold=True)
    pair = _ordered(cell, wanted)
    if pair is None:
        return False
    a, b = pair
    return bool({"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op])


def as_predicates(filters: Mapping[str, Any] | Sequence[Predicate]) -> tuple[Predicate, ...]:
    """Predicates from either shape: a mapping is ``eq`` on every key (the old signature)."""
    if isinstance(filters, Mapping):
        return tuple(Predicate(str(key), "eq", value) for key, value in filters.items())
    return tuple(filters)


def metadata_filter(
    chunks: Iterable[RetrievedChunk],
    filters: Mapping[str, Any] | Sequence[Predicate],
    *,
    required: bool = True,
) -> tuple[RetrievedChunk, ...]:
    """Keep only chunks whose metadata satisfies every predicate.

    ``filters`` is a sequence of ``Predicate`` (typed: ISO dates compare as dates, numbers as
    numbers, ``contains`` and ``icontains`` match a whole item of a multi-valued cell first and
    a substring second), or the old ``{key: value}`` mapping, which means ``eq`` per key.

    Fail closed (the blueprint's metadata-filtering rule): with ``required=True`` a chunk that
    does not carry a predicate's column is dropped rather than assumed to match; with
    ``required=False`` the column is treated as absent and only present columns must match.
    """
    predicates = as_predicates(filters)
    kept: list[RetrievedChunk] = []
    for chunk in chunks:
        ok = True
        for predicate in predicates:
            if predicate.column not in chunk.metadata:
                if required:
                    ok = False
                    break
                continue
            if not matches(predicate, chunk.metadata[predicate.column]):
                ok = False
                break
        if ok:
            kept.append(chunk)
    return tuple(kept)


def filtered_set(
    chunks: Iterable[RetrievedChunk],
    filters: Mapping[str, Any] | Sequence[Predicate],
    *,
    required: bool = True,
    max_filter_rows: int = MAX_FILTER_ROWS,
) -> tuple[RetrievedChunk, ...]:
    """The complete set of chunks matching the predicates: never a top-k sample and never cut.
    More than ``max_filter_rows`` matches raises ``FilterTooWide`` (the caller narrows the
    filter) rather than truncating silently."""
    if max_filter_rows < 1:
        raise ValueError("max_filter_rows must be positive")
    kept = metadata_filter(chunks, filters, required=required)
    if len(kept) > max_filter_rows:
        raise FilterTooWide(len(kept), max_filter_rows)
    return kept


def _row_number(chunk: RetrievedChunk) -> float:
    value = typed(chunk.metadata.get("row"))
    return value if isinstance(value, float) else math.inf


def _last_seen(chunk: RetrievedChunk) -> date | None:
    value = typed(chunk.metadata.get("last_seen"))
    return value if isinstance(value, date) else None


def dedupe(chunks: Sequence[RetrievedChunk], key: str) -> tuple[RetrievedChunk, ...]:
    """One chunk per business key (``requisition``, ``id``...): the one with the latest
    ``last_seen`` when the chunks carry it, else the lowest ``row``, else the lowest
    ``chunk_id``. Chunks without the key (or with an empty one) are kept as they are. The
    output keeps the input order of the survivors."""
    best: dict[str, RetrievedChunk] = {}
    for chunk in chunks:
        value = chunk.metadata.get(key)
        if value is None or str(value).strip() == "":
            continue
        name = str(value).strip().casefold()
        held = best.get(name)
        if held is None or _prefer(chunk, held):
            best[name] = chunk
    survivors = {id(c) for c in best.values()}
    return tuple(
        c
        for c in chunks
        if id(c) in survivors
        or c.metadata.get(key) is None
        or str(c.metadata.get(key)).strip() == ""
    )


def _prefer(candidate: RetrievedChunk, held: RetrievedChunk) -> bool:
    """Whether ``candidate`` replaces ``held`` for the same business key."""
    seen_c, seen_h = _last_seen(candidate), _last_seen(held)
    if seen_c is not None and seen_h is not None and seen_c != seen_h:
        return seen_c > seen_h
    if (seen_c is None) != (seen_h is None):
        return seen_c is not None
    row_c, row_h = _row_number(candidate), _row_number(held)
    if row_c != row_h:
        return row_c < row_h
    return candidate.chunk_id < held.chunk_id


def lexical_overlap(query: str, text: str) -> float:
    """Query coverage ``|q ∩ t| / |q|`` (the deterministic re-ranker): the share of the
    query's tokens the text holds, so a long passage is not penalised for its length (Jaccard
    over the union scored a 300-token row near zero against a 15-token question)."""
    query_terms = set(tokenize(query))
    if not query_terms:
        return 0.0
    text_terms = set(tokenize(text))
    return len(query_terms & text_terms) / len(query_terms)


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
