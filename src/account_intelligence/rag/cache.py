"""Semantic caching: serve repeated questions without retrieval.

A query is embedded, compared against cached queries with cosine
similarity, and served from the cache when the score clears a
strict threshold — bypassing retrieval and generation entirely
(single-digit-millisecond answers for frequently asked questions).

The cache is advisory by construction (the platform rule: Redis is
never the source of truth): a miss, a stale entry or a below-threshold
similarity falls through to the real pipeline. Time is injected —
pure modules never read the wall clock.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Sequence
from typing import Protocol

from account_intelligence.rag.models import QaAnswer


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in [-1, 1]; a zero vector is orthogonal to all."""
    if len(a) != len(b):
        raise ValueError("vectors must share a dimension")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class CacheEntry:
    """One cached answer with the query embedding that produced it."""

    def __init__(self, embedding: tuple[float, ...], answer: QaAnswer, stored_at: float) -> None:
        """Store the embedding, the answer and the insertion time."""
        self.embedding = embedding
        self.answer = answer
        self.stored_at = stored_at


class SemanticCache:
    """In-memory semantic cache with a similarity gate and a TTL.

    Capacity is an LRU: the least recently used entry is evicted
    first, so a flood of novel queries cannot pin old answers.
    ``now`` is injected on every call.
    """

    def __init__(
        self,
        *,
        similarity_threshold: float,
        ttl_seconds: int,
        max_entries: int,
    ) -> None:
        """Configure the gate, the TTL and the capacity."""
        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be within [0, 1]")
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds cannot be negative")
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()

    def _key(self, embedding: Sequence[float]) -> str:
        """A deterministic cache key: the rounded embedding vector."""
        return ",".join(f"{value:.6f}" for value in embedding)

    def lookup(self, embedding: Sequence[float], *, now: float) -> QaAnswer | None:
        """The cached answer when similarity clears the gate and the TTL holds.

        A hit also refreshes recency (LRU). A stale entry is evicted
        and treated as a miss — a cached answer outliving its TTL is a
        correctness bug, not a performance win.
        """
        best: tuple[float, str] | None = None
        for key, entry in self._entries.items():
            if now - entry.stored_at > self.ttl_seconds:
                continue
            similarity = cosine_similarity(embedding, entry.embedding)
            if similarity >= self.similarity_threshold and (best is None or similarity > best[0]):
                best = (similarity, key)
        if best is None:
            return None
        key = best[1]
        self._entries.move_to_end(key)
        return self._entries[key].answer

    def store(self, embedding: Sequence[float], answer: QaAnswer, *, now: float) -> None:
        """Cache ``answer`` for ``embedding``, evicting the LRU entry."""
        key = self._key(embedding)
        self._entries[key] = CacheEntry(tuple(embedding), answer, now)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def invalidate(self, embedding: Sequence[float]) -> bool:
        """Drop the exact-matching entry; True when one existed."""
        key = self._key(embedding)
        return self._entries.pop(key, None) is not None


class EmbedderPort(Protocol):
    """The embedding model shared by the dense arm and the cache."""

    async def embed(self, text: str) -> list[float]:
        """The dense embedding of ``text``."""
        ...


class CacheStorePort(Protocol):
    """The production cache backend (Redis in the blueprint)."""

    async def get(self, key: str) -> bytes | None:
        """The serialized entry, or None when absent or expired."""
        ...

    async def put(self, key: str, body: bytes, ttl_seconds: int) -> None:
        """Store one entry with a TTL."""
        ...
