"""Typed view of ``config/research_files.yaml`` (loaded once): the parser limits the documents
sandbox uses."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from account_intelligence.config import CONFIG_DIR


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ParsingCfg(_M):
    """Unit sizes."""

    rows_per_unit: int = Field(gt=0)
    unit_text_bytes: int = Field(gt=0)
    json_max_document_bytes: int = Field(gt=0)


class ArchiveCfg(_M):
    """Zip bomb and traversal guards."""

    max_depth: int = Field(ge=0)
    max_members: int = Field(gt=0)
    max_total_uncompressed_bytes: int = Field(gt=0)
    max_ratio: float = Field(gt=1)


class AuditCfg(_M):
    """Audit thresholds."""

    max_findings_per_check: int = Field(gt=0)


class ResearchFilesConfig(_M):
    """The whole file."""

    version: str
    status: str
    parsing: ParsingCfg
    archives: ArchiveCfg
    audit: AuditCfg


@lru_cache(maxsize=2)
def load_config(path: Path | None = None) -> ResearchFilesConfig:
    """Load and validate the file."""
    data = yaml.safe_load((path or CONFIG_DIR / "research_files.yaml").read_text())
    return ResearchFilesConfig.model_validate(data)
