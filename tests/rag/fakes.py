"""Deterministic fakes for every RAG port (no model, store or Redis)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from account_intelligence.platform.ports import LlmResult, LlmUsage
from account_intelligence.rag.guardrails import SafetyDecision
from account_intelligence.rag.models import RetrievedChunk

GENERATOR_TIER = "generator"


class FakeEmbedder:
    """Deterministic embedder: distinct texts get near-orthogonal vectors."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        """A one-hot vector at a stable per-process index for ``text``."""
        self.calls.append(text)
        vector = [0.0] * 64
        vector[hash(text) % 64] = 1.0
        return vector


class FakeLlm:
    """Scripts ``LlmPort.complete`` responses and records every call."""

    def __init__(self, responses: Sequence[str] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[str, str, str]] = []

    async def complete(self, tier: str, system: str, user: str, *, max_tokens: int) -> LlmResult:
        """Pop the next scripted response, or fail loudly."""
        self.calls.append((tier, system, user))
        if not self.responses:
            raise AssertionError("unrecorded FakeLlm request")
        return LlmResult(
            text=self.responses.pop(0),
            model="fake",
            usage=LlmUsage(input_tokens=1, output_tokens=1),
        )


class FakeDenseRetriever:
    """Dense arm: pops one scripted hit-list per search call."""

    def __init__(self, script: Sequence[Sequence[RetrievedChunk]] | None = None) -> None:
        self.script = [list(hits) for hits in (script or [])]
        self.calls: list[tuple[list[float], int, dict[str, Any]]] = []

    async def search(
        self, embedding: Sequence[float], top_k: int, filters: Mapping[str, Any]
    ) -> list[RetrievedChunk]:
        """Return the next scripted hits for one embedding query."""
        self.calls.append((list(embedding), top_k, dict(filters)))
        if not self.script:
            return []
        return list(self.script.pop(0))


class FakeSparseRetriever:
    """Sparse arm: pops one scripted hit-list per search call."""

    def __init__(self, script: Sequence[Sequence[RetrievedChunk]] | None = None) -> None:
        self.script = [list(hits) for hits in (script or [])]
        self.calls: list[tuple[str, int, dict[str, Any]]] = []

    async def search(
        self, query: str, top_k: int, filters: Mapping[str, Any]
    ) -> list[RetrievedChunk]:
        """Return the next scripted hits for one keyword query."""
        self.calls.append((query, top_k, dict(filters)))
        if not self.script:
            return []
        return list(self.script.pop(0))


class FakeGraphRetriever:
    """Graph arm: pops one scripted hit-list per traversal."""

    def __init__(self, script: Sequence[Sequence[RetrievedChunk]] | None = None) -> None:
        self.script = [list(hits) for hits in (script or [])]
        self.calls: list[tuple[list[str], int]] = []

    async def search(self, seeds: Sequence[str], max_hops: int) -> list[Any]:
        """Return the next scripted chunks reachable from ``seeds``."""
        self.calls.append((list(seeds), max_hops))
        if not self.script:
            return []
        return list(self.script.pop(0))


class FakeReranker:
    """Cross-encoder arm: passes chunks through, optionally scripted."""

    def __init__(self, script: Sequence[Sequence[RetrievedChunk]] | None = None) -> None:
        self.script = [list(hits) for hits in (script or [])]
        self.calls: list[tuple[str, list[str]]] = []

    async def rerank(self, query: str, chunks: Sequence[RetrievedChunk]) -> list[RetrievedChunk]:
        """Return the scripted order, or the input order unchanged."""
        self.calls.append((query, [chunk.chunk_id for chunk in chunks]))
        if self.script:
            return list(self.script.pop(0))
        return list(chunks)


class FakeJudge:
    """Reflection model: returns one scripted score triple per critique."""

    def __init__(self, scores: Sequence[tuple[float, float, float]] | None = None) -> None:
        self.scores = list(scores or [])
        self.calls: list[tuple[str, list[str]]] = []

    async def critique(
        self, query: str, chunks: Sequence[RetrievedChunk]
    ) -> tuple[float, float, float]:
        """Return the next scripted (relevance, completeness, quality)."""
        self.calls.append((query, [chunk.chunk_id for chunk in chunks]))
        if self.scores:
            return self.scores.pop(0)
        return (1.0, 1.0, 1.0)


class FakeGuardrail:
    """Safety classifier that blocks a scripted set of queries."""

    def __init__(self, blocked: frozenset[str] = frozenset()) -> None:
        self.blocked = blocked
        self.calls: list[str] = []

    async def classify(self, text: str) -> SafetyDecision:
        """Allow everything except the scripted blocked queries."""
        self.calls.append(text)
        if text in self.blocked:
            return SafetyDecision(False, "scripted block")
        return SafetyDecision(True)


class FakeClock:
    """Injectable clock for cache TTL tests."""

    def __init__(self, now: float = 0.0) -> None:
        self.seconds = now

    def now(self) -> datetime:
        """The injected time as an aware UTC datetime."""
        return datetime.fromtimestamp(self.seconds, tz=timezone.utc)

    def advance(self, seconds: float) -> None:
        """Move the injected time forward."""
        self.seconds += seconds
