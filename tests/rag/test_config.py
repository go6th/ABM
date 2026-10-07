"""Configuration loading: the frozen, validated knobs of the RAG loop."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from account_intelligence.rag.config import (
    AnswerConfig,
    CacheConfig,
    LoopConfig,
    RagConfig,
    ReflectionConfig,
    RetrievalConfig,
    load_rag_config,
)


def test_load_defaults_match_the_blueprint() -> None:
    """Every knob the modules rely on parses with the documented value."""
    config = load_rag_config()
    assert config.version == "1.0.0"
    assert config.retrieval == RetrievalConfig(
        top_k=8,
        dense_weight=0.7,
        sparse_weight=0.3,
        rrf_k=60,
        lexical_weight=0.25,
        min_score=0.1,
        metadata_required=True,
    )
    assert config.cache == CacheConfig(
        similarity_threshold=0.92, ttl_seconds=3600, max_entries=10000
    )
    assert config.reflection == ReflectionConfig(
        relevance_threshold=0.6,
        completeness_threshold=0.7,
        quality_threshold=0.5,
        max_passes=3,
    )
    assert config.loop == LoopConfig(max_iterations=5)
    assert config.answer == AnswerConfig(confidence_floor=0.5, relevance_floor=0.85)


def test_load_explicit_path() -> None:
    """A path argument overrides the packaged default."""
    path = Path(__file__).parent.parent.parent / "config" / "rag.yaml"
    assert load_rag_config(path).retrieval.top_k == 8


def test_missing_section_raises(tmp_path: Path) -> None:
    """A dropped section is a configuration error, not a default."""
    path = tmp_path / "rag.yaml"
    path.write_text(yaml.safe_dump({"version": "1.0.0", "retrieval": {}}))
    with pytest.raises(ValueError, match="cache"):
        load_rag_config(path)


def test_missing_key_raises(tmp_path: Path) -> None:
    """A dropped knob is a configuration error with the key named."""
    path = tmp_path / "rag.yaml"
    raw = yaml.safe_load((Path(__file__).parent.parent.parent / "config" / "rag.yaml").read_text())
    del raw["retrieval"]["rrf_k"]
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="rrf_k"):
        load_rag_config(path)


def test_non_mapping_raises(tmp_path: Path) -> None:
    """A YAML list is not a configuration."""
    path = tmp_path / "rag.yaml"
    path.write_text("- a\n- b\n")
    with pytest.raises(ValueError, match="mapping"):
        load_rag_config(path)


def test_config_is_frozen() -> None:
    """The loaded configuration cannot be mutated at runtime."""
    config = load_rag_config()
    with pytest.raises(Exception):
        config.retrieval.top_k = 4  # type: ignore[misc]


def test_returns_rag_config() -> None:
    """The loader's return type is the frozen dataclass."""
    assert isinstance(load_rag_config(), RagConfig)
