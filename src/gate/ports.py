"""Service boundaries for quality-gate inputs, checks, and immutable results.

Guarantees: missing production adapters raise NotConfigured and cannot yield a
success-shaped fallback (GUARANTEE(test_unconfigured_ports_are_explicit)).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from gate.errors import NotConfigured
from gate.models import (
    BuildManifest,
    CheckResult,
    GateResult,
    GateStep,
    PackageFile,
    PackageSnapshot,
)


class PackageReader(Protocol):
    async def read_for_run(self, tenant_id: UUID, run_id: UUID) -> PackageSnapshot: ...


class TrackerRowsPort(Protocol):
    async def rows_for_run(self, tenant_id: UUID, run_id: UUID) -> tuple[object, ...]: ...

    async def tally_from_rows(self, rows: tuple[object, ...]) -> dict[str, int]: ...


class NumberRegistryPort(Protocol):
    async def numbers_for_run(self, tenant_id: UUID, run_id: UUID) -> tuple[object, ...]: ...


class SourceRegistryPort(Protocol):
    async def sources_for_run(self, tenant_id: UUID, run_id: UUID) -> tuple[object, ...]: ...


class ExcludedPeoplePort(Protocol):
    async def names_for_run(self, tenant_id: UUID, run_id: UUID) -> tuple[str, ...]: ...


class PlanPort(Protocol):
    async def open_rows_closable_within_cap(
        self, tenant_id: UUID, run_id: UUID
    ) -> tuple[object, ...]: ...


class ConverterPort(Protocol):
    async def render_docx(self, source: PackageFile) -> PackageFile: ...

    async def recalculate_workbook(self, source: PackageFile) -> PackageFile: ...


class PdfTextPort(Protocol):
    async def page_text(self, pdf: PackageFile) -> tuple[str, ...]: ...


class GateStore(Protocol):
    async def insert_result(self, result: GateResult) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class GateCheckPort(Protocol):
    async def run(
        self,
        *,
        step: GateStep,
        snapshot: PackageSnapshot,
        target: PackageFile | None,
    ) -> CheckResult:
        """Run exactly one check and return its explicit outcome."""
        ...


class UnconfiguredPackageReader:
    async def read_for_run(self, tenant_id: UUID, run_id: UUID) -> PackageSnapshot:
        raise NotConfigured("read-only package reader")


class UnconfiguredGateCheckPort:
    async def run(
        self,
        *,
        step: GateStep,
        snapshot: PackageSnapshot,
        target: PackageFile | None,
    ) -> CheckResult:
        del step, snapshot, target
        raise NotConfigured("legacy quality check adapters")


class UnconfiguredGateStore:
    async def insert_result(self, result: GateResult) -> None:
        del result
        raise NotConfigured("insert-only gate result store")


class UnconfiguredTrackerRowsPort:
    async def rows_for_run(self, tenant_id: UUID, run_id: UUID) -> tuple[object, ...]:
        del tenant_id, run_id
        raise NotConfigured("tracker rows adapter")

    async def tally_from_rows(self, rows: tuple[object, ...]) -> dict[str, int]:
        del rows
        raise NotConfigured("tracker tally adapter")


class UnconfiguredNumberRegistryPort:
    async def numbers_for_run(self, tenant_id: UUID, run_id: UUID) -> tuple[object, ...]:
        del tenant_id, run_id
        raise NotConfigured("number registry adapter")


class UnconfiguredSourceRegistryPort:
    async def sources_for_run(self, tenant_id: UUID, run_id: UUID) -> tuple[object, ...]:
        del tenant_id, run_id
        raise NotConfigured("source registry adapter")


class UnconfiguredExcludedPeoplePort:
    async def names_for_run(self, tenant_id: UUID, run_id: UUID) -> tuple[str, ...]:
        del tenant_id, run_id
        raise NotConfigured("excluded people adapter")


class UnconfiguredPlanPort:
    async def open_rows_closable_within_cap(
        self, tenant_id: UUID, run_id: UUID
    ) -> tuple[object, ...]:
        del tenant_id, run_id
        raise NotConfigured("run plan adapter")


class UnconfiguredConverterPort:
    async def render_docx(self, source: PackageFile) -> PackageFile:
        del source
        raise NotConfigured("document converter")

    async def recalculate_workbook(self, source: PackageFile) -> PackageFile:
        del source
        raise NotConfigured("workbook recalculator")


class UnconfiguredPdfTextPort:
    async def page_text(self, pdf: PackageFile) -> tuple[str, ...]:
        del pdf
        raise NotConfigured("PDF text extractor")


class UnconfiguredClock:
    def now(self) -> datetime:
        raise NotConfigured("gate clock")


class StaticBuildManifest:
    """Marker type for structural typing of manifest values."""

    manifest_type = BuildManifest
