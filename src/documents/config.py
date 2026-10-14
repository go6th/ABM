"""Typed view of ``config/research_input_modes.yaml`` (loaded once).

The 33 tracker rows are split between rows uploaded material can answer (``file_rows``, with
the extraction categories that answer each) and rows that need public sources
(``public_only``); together they must cover the checklist exactly.

GUARANTEE(test_input_mode_config_covers_every_tracker_row)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from account_intelligence.config import CONFIG_DIR


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UploadCfg(_M):
    """The fallback per-file limit, the browser's chunk size and the accepted types."""

    max_file_bytes: int = Field(gt=0)
    chunk_bytes: int = Field(gt=0, multiple_of=262144)
    upload_ttl_minutes: int = Field(gt=0)
    document_types: tuple[str, ...]
    audio_types: tuple[str, ...]


class ParsingCfg(_M):
    """How a document is split into parts, the per-part limits and the processing lease."""

    part_bytes: int = Field(gt=0)
    pages_per_part: int = Field(gt=0)
    max_parallel_parts: int = Field(gt=0)
    max_pages: int = Field(gt=0)
    max_passages: int = Field(gt=0, lt=10_000_000)  # a part's passages fit its number range
    max_text_bytes: int = Field(gt=0)
    write_batch: int = Field(gt=0)
    index_batch: int = Field(gt=0)
    lease_ttl_s: int = Field(gt=0)
    orphan_after_s: int = Field(gt=0)


class SandboxCfg(_M):
    """Resource limits of the parser subprocess."""

    timeout_s: int = Field(gt=0)
    cpu_s: int = Field(gt=0)
    memory_mb: int = Field(gt=64)


class ChunkingCfg(_M):
    """Passage sizes."""

    chars_per_token: float = Field(gt=0)
    document_min_tokens: int = Field(gt=0)
    document_max_tokens: int = Field(gt=0)
    overlap_fraction: float = Field(ge=0, lt=0.5)
    transcript_min_s: int = Field(gt=0)
    transcript_max_s: int = Field(gt=0)


class EmbeddingCfg(_M):
    """The embedding model and its batch size."""

    model: str
    dimensions: int = Field(gt=0)
    batch: int = Field(gt=0)


class RedactionCfg(_M):
    """The tier a tenant gets until an admin chooses one."""

    default_tier: str = Field(pattern="^(strict|standard|minimal)$")


class CompanyCfg(_M):
    """Company resolution."""

    free_mail_domains: tuple[str, ...]
    ambiguity_margin: float = Field(gt=0, lt=1)
    min_score: float = Field(gt=0)


class ExtractionCfg(_M):
    """Fact extraction from passages."""

    task_kind: str
    max_chars_per_call: int = Field(gt=1000)
    max_passages_per_call: int = Field(gt=0)
    categories: tuple[str, ...]


class LeakCfg(_M):
    """The leak check thresholds."""

    ngram_words: int = Field(ge=4)
    max_ngram_hits: int = Field(ge=0)
    embedding_threshold: float = Field(gt=0, le=1)
    min_paragraph_words: int = Field(gt=0)


class TrackerCfg(_M):
    """How uploaded material maps onto the tracker rows."""

    file_rows: dict[str, tuple[str, ...]]
    public_only: tuple[str, ...]

    @model_validator(mode="after")
    def _disjoint(self) -> TrackerCfg:
        both = set(self.file_rows) & set(self.public_only)
        if both:
            raise ValueError(f"rows both file and public only: {sorted(both)}")
        return self


class InputModesConfig(_M):
    """The whole file."""

    version: str
    status: str
    upload: UploadCfg
    parsing: ParsingCfg
    sandbox: SandboxCfg
    chunking: ChunkingCfg
    embedding: EmbeddingCfg
    redaction: RedactionCfg
    company: CompanyCfg
    extraction: ExtractionCfg
    leak: LeakCfg
    tracker: TrackerCfg

    @model_validator(mode="after")
    def _categories(self) -> InputModesConfig:
        known = set(self.extraction.categories)
        for row, cats in self.tracker.file_rows.items():
            if set(cats) - known:
                raise ValueError(f"row {row} names unknown categories {sorted(set(cats) - known)}")
        return self

    def rows_for(self, category: str) -> tuple[str, ...]:
        """The tracker rows a fact of ``category`` answers."""
        return tuple(r for r, cats in self.tracker.file_rows.items() if category in cats)


@lru_cache(maxsize=2)
def load_config(path: Path | None = None) -> InputModesConfig:
    """Load and validate the file."""
    data = yaml.safe_load((path or CONFIG_DIR / "research_input_modes.yaml").read_text())
    return InputModesConfig.model_validate(data)
