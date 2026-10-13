"""Typed view of ``config/research_files.yaml`` (loaded once)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from account_intelligence.config import CONFIG_DIR


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UploadCfg(_M):
    """Upload limits."""

    max_file_bytes: int = Field(gt=0)
    part_bytes: int = Field(ge=1024)
    upload_ttl_hours: int = Field(gt=0)


class ParsingCfg(_M):
    """Unit sizes and the processing lease."""

    rows_per_unit: int = Field(gt=0)
    unit_text_bytes: int = Field(gt=0)
    max_indexed_text_bytes: int = Field(gt=0)
    excerpt_rows: int = Field(ge=0)
    json_max_document_bytes: int = Field(gt=0)
    lease_ttl_s: int = Field(gt=0)
    orphan_after_s: int = Field(gt=0)


class ArchiveCfg(_M):
    """Zip bomb and traversal guards."""

    max_depth: int = Field(ge=0)
    max_members: int = Field(gt=0)
    max_total_uncompressed_bytes: int = Field(gt=0)
    max_ratio: float = Field(gt=1)


class AuditCfg(_M):
    """Audit thresholds."""

    max_findings_per_check: int = Field(gt=0)
    missing_warn_fraction: float = Field(gt=0, le=1)
    outlier_z: float = Field(gt=0)
    outlier_min_observations: int = Field(gt=1)
    duplicate_rows_checked: int = Field(ge=0)
    pii_scan: bool


class ProfileCfg(_M):
    """Column profile limits."""

    top_values: int = Field(gt=0)
    distinct_cap: int = Field(gt=0)
    sample_size: int = Field(gt=0)
    max_correlated_columns: int = Field(ge=0)
    histogram_bins: int = Field(gt=1)


class ModelsCfg(_M):
    """Model use and cost control."""

    tasks: dict[str, str]  # task -> task kind registered in config/model_routing.yaml
    max_prompt_chars: int = Field(gt=1000)
    approval_limit_usd: float = Field(ge=0)
    chars_per_token: float = Field(gt=0)


class ResearchFilesConfig(_M):
    """The whole file."""

    version: str
    status: str
    upload: UploadCfg
    parsing: ParsingCfg
    archives: ArchiveCfg
    audit: AuditCfg
    profile: ProfileCfg
    models: ModelsCfg


@lru_cache(maxsize=2)
def load_config(path: Path | None = None) -> ResearchFilesConfig:
    """Load and validate the file."""
    data = yaml.safe_load((path or CONFIG_DIR / "research_files.yaml").read_text())
    return ResearchFilesConfig.model_validate(data)
