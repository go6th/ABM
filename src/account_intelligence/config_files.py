"""One typed loader for every configuration file (P0-5: the checklist and the output spec are
configuration, not prose). ``load_all`` validates each file through its typed model and returns
the content hashes, so startup fails fast on a bad file and a run can pin what it used.

Files and their models: checklist and tracker rules (``tracker.config``), workflow
(``workflow.spec``), grading (``grading.config``), providers (``connectors.settings``), model
routes (``platform.llm``), job pipeline (``jobs.config``) and the output spec (here). The billing
proposal that used to be validated here (``billing.yaml``) had no reader of its values and moved to
``docs/registers/billing.yaml`` (ADR-0136).
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from account_intelligence.config import CONFIG_DIR


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Deliverable(_Model):
    """One file of the standing deliverable set (PRD 3.4)."""

    id: str
    filename: str
    format: Literal["docx", "xlsx", "pptx", "zip"]
    audience: Literal["client", "operator_only"]
    contents: tuple[str, ...] = Field(min_length=1)
    includes: tuple[str, ...] = ()

    @field_validator("filename")
    @classmethod
    def _placeholder(cls, value: str) -> str:
        if "{account}" not in value:
            raise ValueError("deliverable file names carry the {account} placeholder")
        return value


class GateCheck(_Model):
    """One blocking quality-gate check."""

    id: str
    description: str


class DocumentRules(_Model):
    """Format rules every document follows."""

    page_size: str
    banned_characters: tuple[str, ...]


class OutputSpec(_Model):
    """``config/output_spec.yaml``: deliverables, on-request extras, the gate and document rules."""

    version: str
    deliverables: tuple[Deliverable, ...]
    on_request_only: tuple[str, ...] = ()
    quality_gate: tuple[GateCheck, ...] = Field(min_length=1)
    document_rules: DocumentRules

    @model_validator(mode="after")
    def _consistent(self) -> OutputSpec:
        ids = [d.id for d in self.deliverables]
        if len(set(ids)) != len(ids):
            raise ValueError("deliverable ids must be unique")
        for d in self.deliverables:
            if not set(d.includes) <= set(ids):
                raise ValueError(f"deliverable {d.id} includes an unknown deliverable")
        if len({g.id for g in self.quality_gate}) != len(self.quality_gate):
            raise ValueError("quality gate ids must be unique")
        return self


def _yaml(name: str, directory: Path) -> tuple[object, str]:
    raw = (directory / name).read_bytes()
    return yaml.safe_load(raw), hashlib.sha256(raw).hexdigest()


def load_output_spec(directory: Path | None = None) -> OutputSpec:
    """Validate ``output_spec.yaml``."""
    data, _ = _yaml("output_spec.yaml", directory or CONFIG_DIR)
    return OutputSpec.model_validate(data)


@lru_cache(maxsize=1)
def load_all() -> dict[str, str]:
    """Validate every configuration file through its typed model; file name to SHA-256."""
    from account_intelligence.connectors.settings import load_providers_config
    from account_intelligence.grading.config import load_grading_config
    from account_intelligence.platform.llm import load_routes
    from account_intelligence.tracker.config import load_checklist, load_tracker_config
    from account_intelligence.workflow.spec import load_spec
    from jobs.config import load_jobs_config

    load_checklist()
    load_tracker_config()
    load_spec()
    load_grading_config()
    load_providers_config()
    load_routes()
    load_jobs_config()
    load_output_spec()
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(CONFIG_DIR.glob("*.yaml"))
    }
