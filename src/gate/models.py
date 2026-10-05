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
    """Gate stages in mandatory execution order; the pipeline runs S0 through S5."""

    INTAKE = "S0"
    RENDER = "S1"
    ROWCHECK = "S2"
    DOC_CHECKS = "S3"
    THIRD_PARTY_SCAN = "S4"
    SANITISER_CHECK = "S5"


class Verdict(StrEnum):
    """Terminal package verdict: PASS may be delivered, FAIL blocks it."""

    PASS = "PASS"
    FAIL = "FAIL"


class CheckStatus(StrEnum):
    """Per-check outcome; NOT_RUN marks checks skipped after an earlier blocking failure."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"


@dataclass(frozen=True)
class PackageFile:
    """One immutable file in a build package, pinned by name and exact bytes."""

    name: str
    content: bytes


@dataclass(frozen=True)
class BuildManifest:
    """Manifest binding a package id to its exact files and the files the gate must find.

    ``previous_package_id`` links a redelivery to the package it replaces so verdicts
    stay auditable across reruns.
    """

    package_id: str
    files: tuple[PackageFile, ...]
    expected_files: tuple[str, ...]
    internal_only_files: tuple[str, ...]
    previous_package_id: str | None = None
    delivery_note: str | None = None


@dataclass(frozen=True)
class PackageSnapshot:
    """Tenant-scoped manifest snapshot; the only package input a check may trust."""

    tenant_id: UUID
    run_id: UUID
    manifest: BuildManifest


@dataclass(frozen=True)
class CheckExecution:
    """A check's outcome plus the files it generated for consumption by later steps."""

    result: CheckResult
    generated_files: tuple[PackageFile, ...] = ()


@dataclass(frozen=True)
class CheckResult:
    """One check's explicit outcome, bound to its step so results order deterministically."""

    step: GateStep
    check: str
    status: CheckStatus
    reason: str | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class Defect:
    """Blocking defect with a redacted preview and the rerun step that must clear it."""

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
    """Immutable verdict bound to the exact package and config hashes it was computed on."""

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
