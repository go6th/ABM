"""Load and validate ``config/rag.yaml`` (the single source of thresholds).

The configuration is frozen at load time; modules receive the parsed
dataclass and never read YAML or the environment themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR


@dataclass(frozen=True)
class RetrievalConfig:
    """Hybrid retrieval knobs: weights, fusion constant, top-k, fail-closed."""

    top_k: int
    dense_weight: float
    sparse_weight: float
    rrf_k: int
    lexical_weight: float
    min_score: float
    metadata_required: bool


@dataclass(frozen=True)
class CacheConfig:
    """Semantic cache knobs: similarity gate, TTL and capacity."""

    similarity_threshold: float
    ttl_seconds: int
    max_entries: int


@dataclass(frozen=True)
class ReflectionConfig:
    """Reflection loop knobs: the three critique thresholds and the pass cap."""

    relevance_threshold: float
    completeness_threshold: float
    quality_threshold: float
    max_passes: int


@dataclass(frozen=True)
class LoopConfig:
    """Circuit breaker: total retrieval passes per query (OWASP LLM10)."""

    max_iterations: int


@dataclass(frozen=True)
class AnswerConfig:
    """Generation knobs: confidence floor and the answer-relevance target."""

    confidence_floor: float
    relevance_floor: float


@dataclass(frozen=True)
class RagConfig:
    """The validated, frozen RAG configuration."""

    version: str
    retrieval: RetrievalConfig
    cache: CacheConfig
    reflection: ReflectionConfig
    loop: LoopConfig
    answer: AnswerConfig


def _section(raw: dict[str, Any], name: str, keys: tuple[str, ...]) -> dict[str, Any]:
    """One validated section: every key present, nothing extra."""
    section = raw.get(name)
    if not isinstance(section, dict):
        raise ValueError(f"rag config is missing the {name} section")
    missing = set(keys) - set(section)
    if missing:
        raise ValueError(f"rag config {name} is missing keys: {sorted(missing)}")
    return section


def load_rag_config(path: Path | None = None) -> RagConfig:
    """Read ``rag.yaml`` and validate every knob the modules rely on."""
    raw: dict[str, Any] = yaml.safe_load((path or CONFIG_DIR / "rag.yaml").read_text())
    if not isinstance(raw, dict):
        raise ValueError("rag config must be a mapping")
    retrieval = _section(
        raw,
        "retrieval",
        (
            "top_k",
            "dense_weight",
            "sparse_weight",
            "rrf_k",
            "lexical_weight",
            "min_score",
            "metadata_required",
        ),
    )
    cache = _section(raw, "cache", ("similarity_threshold", "ttl_seconds", "max_entries"))
    reflection = _section(
        raw,
        "reflection",
        (
            "relevance_threshold",
            "completeness_threshold",
            "quality_threshold",
            "max_passes",
        ),
    )
    loop = _section(raw, "loop", ("max_iterations",))
    answer = _section(raw, "answer", ("confidence_floor", "relevance_floor"))
    return RagConfig(
        version=str(raw.get("version", "unknown")),
        retrieval=RetrievalConfig(
            top_k=int(retrieval["top_k"]),
            dense_weight=float(retrieval["dense_weight"]),
            sparse_weight=float(retrieval["sparse_weight"]),
            rrf_k=int(retrieval["rrf_k"]),
            lexical_weight=float(retrieval["lexical_weight"]),
            min_score=float(retrieval["min_score"]),
            metadata_required=bool(retrieval["metadata_required"]),
        ),
        cache=CacheConfig(
            similarity_threshold=float(cache["similarity_threshold"]),
            ttl_seconds=int(cache["ttl_seconds"]),
            max_entries=int(cache["max_entries"]),
        ),
        reflection=ReflectionConfig(
            relevance_threshold=float(reflection["relevance_threshold"]),
            completeness_threshold=float(reflection["completeness_threshold"]),
            quality_threshold=float(reflection["quality_threshold"]),
            max_passes=int(reflection["max_passes"]),
        ),
        loop=LoopConfig(max_iterations=int(loop["max_iterations"])),
        answer=AnswerConfig(
            confidence_floor=float(answer["confidence_floor"]),
            relevance_floor=float(answer["relevance_floor"]),
        ),
    )
