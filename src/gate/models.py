"""Immutable quality-gate input and result models.

Guarantees: results identify the exact package bytes checked and are ordered
deterministically (GUARANTEE(test_gate_is_deterministic_and_bound_to_input_hash)).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID


class GateStep(StrEnum):
    INTAKE = "S0"
    RENDER = "S1"
    ROWCHECK = "S2"
    DOC_CHECKS = "S3"
    THIRD_PARTY_SCAN = "S4"
    SANITISER_CHECK = "S5"


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"


@dataclass(frozen=True)
class PackageFile:
    name: str
    content: bytes


@dataclass(frozen=True)
class BuildManifest:
    package_id: str
    files: tuple[PackageFile, ...]
    expected_files: tuple[str, ...]
    internal_only_files: tuple[str, ...]
    previous_package_id: str | None = None
    delivery_note: str | None = None


@dataclass(frozen=True)
class PackageSnapshot:
    tenant_id: UUID
    run_id: UUID
    manifest: BuildManifest


@dataclass(frozen=True)
class CheckExecution:
    result: CheckResult
    generated_files: tuple[PackageFile, ...] = ()


@dataclass(frozen=True)
class CheckResult:
    step: GateStep
    check: str
    status: CheckStatus
    reason: str | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class Defect:
    defect_id: str
    code: str
    file: str | None
    part: str | None
    location: str | None
    redacted_preview: str
    content_hash: str | None
    rerun_step: GateStep
    severity: Literal["blocking"] = "blocking"


@dataclass(frozen=True)
class GateResult:
    tenant_id: UUID
    run_id: UUID
    package_hash: str
    config_hash: str
    verdict: Verdict
    checks: tuple[CheckResult, ...]
    defects: tuple[Defect, ...]
    truncated: bool
    started_at: datetime
    finished_at: datetime
    runner: str
