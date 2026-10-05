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
    """Read-only access to the package snapshot a gate run validates."""

    async def read_for_run(self, tenant_id: UUID, run_id: UUID) -> PackageSnapshot:
        """Return the immutable package snapshot for this tenant's run."""
        ...


class TrackerRowsPort(Protocol):
    """Access to tracker rows so gate checks verify delivery counts against them."""

    async def rows_for_run(self, tenant_id: UUID, run_id: UUID) -> tuple[object, ...]:
        """Return the tracker rows recorded for this run."""
        ...

    async def tally_from_rows(self, rows: tuple[object, ...]) -> dict[str, int]:
        """Fold tracker rows into the per-kind delivery tally the row checks compare."""
        ...


class NumberRegistryPort(Protocol):
    """Access to the number registry so checks can trace every delivered number."""

    async def numbers_for_run(self, tenant_id: UUID, run_id: UUID) -> tuple[object, ...]:
        """Return the registered numbers for this run."""
        ...


class SourceRegistryPort(Protocol):
    """Access to the source registry so checks can verify source lineage claims."""

    async def sources_for_run(self, tenant_id: UUID, run_id: UUID) -> tuple[object, ...]:
        """Return the registered sources for this run."""
        ...


class ExcludedPeoplePort(Protocol):
    """Access to excluded people so checks can prove none of them leaked into output."""

    async def names_for_run(self, tenant_id: UUID, run_id: UUID) -> tuple[str, ...]:
        """Return the names excluded from this run's people list."""
        ...


class PlanPort(Protocol):
    """Access to the run plan so checks respect the credit cap when closing rows."""

    async def open_rows_closable_within_cap(
        self, tenant_id: UUID, run_id: UUID
    ) -> tuple[object, ...]:
        """Return the open plan rows that may still be closed within the remaining cap."""
        ...


class ConverterPort(Protocol):
    """Rendering boundary so checks always inspect the exact artifacts clients receive."""

    async def render_docx(self, source: PackageFile) -> PackageFile:
        """Render the source file to DOCX and return the produced artifact."""
        ...

    async def recalculate_workbook(self, source: PackageFile) -> PackageFile:
        """Recalculate the workbook's formulas and return the produced artifact."""
        ...


class PdfTextPort(Protocol):
    """Text extraction so checks can scan rendered PDFs for leaked internal content."""

    async def page_text(self, pdf: PackageFile) -> tuple[str, ...]:
        """Return the extracted text of each page, in page order."""
        ...


class GateStore(Protocol):
    """Insert-only persistence so gate results are an append-only audit trail."""

    async def insert_result(self, result: GateResult) -> None:
        """Persist one immutable gate result; updates are not part of this contract."""
        ...


class Clock(Protocol):
    """Time source so gate timing is testable and wall-clock reads are explicit."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        ...


class GateCheckPort(Protocol):
    """One gate check; the pipeline runs one port instance per check step."""

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
    """Fail-closed placeholder raising NotConfigured until the reader adapter is wired."""

    async def read_for_run(self, tenant_id: UUID, run_id: UUID) -> PackageSnapshot:
        """Refuse to serve a package snapshot without a production reader."""
        del tenant_id, run_id
        raise NotConfigured("read-only package reader")


class UnconfiguredGateCheckPort:
    """Fail-closed placeholder raising NotConfigured until legacy check adapters exist."""

    async def run(
        self,
        *,
        step: GateStep,
        snapshot: PackageSnapshot,
        target: PackageFile | None,
    ) -> CheckResult:
        """Refuse to produce a check outcome without a production adapter."""
        del step, snapshot, target
        raise NotConfigured("legacy quality check adapters")


class UnconfiguredGateStore:
    """Fail-closed placeholder raising NotConfigured until the result store is wired."""

    async def insert_result(self, result: GateResult) -> None:
        """Refuse to persist results without a production store."""
        del result
        raise NotConfigured("insert-only gate result store")


class UnconfiguredTrackerRowsPort:
    """Fail-closed placeholder raising NotConfigured until tracker adapters exist."""

    async def rows_for_run(self, tenant_id: UUID, run_id: UUID) -> tuple[object, ...]:
        """Refuse to serve tracker rows without a production adapter."""
        del tenant_id, run_id
        raise NotConfigured("tracker rows adapter")

    async def tally_from_rows(self, rows: tuple[object, ...]) -> dict[str, int]:
        """Refuse to tally rows without a production adapter."""
        del rows
        raise NotConfigured("tracker tally adapter")


class UnconfiguredNumberRegistryPort:
    """Fail-closed placeholder raising NotConfigured until the number registry is wired."""

    async def numbers_for_run(self, tenant_id: UUID, run_id: UUID) -> tuple[object, ...]:
        """Refuse to serve registered numbers without a production adapter."""
        del tenant_id, run_id
        raise NotConfigured("number registry adapter")


class UnconfiguredSourceRegistryPort:
    """Fail-closed placeholder raising NotConfigured until the source registry is wired."""

    async def sources_for_run(self, tenant_id: UUID, run_id: UUID) -> tuple[object, ...]:
        """Refuse to serve registered sources without a production adapter."""
        del tenant_id, run_id
        raise NotConfigured("source registry adapter")


class UnconfiguredExcludedPeoplePort:
    """Fail-closed placeholder raising NotConfigured until the exclusion source is wired."""

    async def names_for_run(self, tenant_id: UUID, run_id: UUID) -> tuple[str, ...]:
        """Refuse to serve excluded names without a production adapter."""
        del tenant_id, run_id
        raise NotConfigured("excluded people adapter")


class UnconfiguredPlanPort:
    """Fail-closed placeholder raising NotConfigured until the run plan adapter is wired."""

    async def open_rows_closable_within_cap(
        self, tenant_id: UUID, run_id: UUID
    ) -> tuple[object, ...]:
        """Refuse to serve plan rows without a production adapter."""
        del tenant_id, run_id
        raise NotConfigured("run plan adapter")


class UnconfiguredConverterPort:
    """Fail-closed placeholder raising NotConfigured until render adapters exist."""

    async def render_docx(self, source: PackageFile) -> PackageFile:
        """Refuse to render DOCX without a production converter."""
        del source
        raise NotConfigured("document converter")

    async def recalculate_workbook(self, source: PackageFile) -> PackageFile:
        """Refuse to recalculate workbooks without a production converter."""
        del source
        raise NotConfigured("workbook recalculator")


class UnconfiguredPdfTextPort:
    """Fail-closed placeholder raising NotConfigured until the PDF extractor is wired."""

    async def page_text(self, pdf: PackageFile) -> tuple[str, ...]:
        """Refuse to extract PDF text without a production extractor."""
        del pdf
        raise NotConfigured("PDF text extractor")


class UnconfiguredClock:
    """Fail-closed placeholder raising NotConfigured until a clock is injected."""

    def now(self) -> datetime:
        """Refuse to report time without a production clock."""
        raise NotConfigured("gate clock")


class StaticBuildManifest:
    """Marker type for structural typing of manifest values."""

    manifest_type = BuildManifest
