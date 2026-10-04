from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from gate.errors import GateCheckError, GateInputError, NotConfigured
from gate.models import (
    BuildManifest,
    CheckResult,
    CheckStatus,
    Defect,
    GateResult,
    GateStep,
    PackageFile,
    PackageSnapshot,
    Verdict,
)
from gate.pipeline import GatePipeline
from gate.ports import (
    StaticBuildManifest,
    UnconfiguredClock,
    UnconfiguredConverterPort,
    UnconfiguredExcludedPeoplePort,
    UnconfiguredGateCheckPort,
    UnconfiguredGateStore,
    UnconfiguredNumberRegistryPort,
    UnconfiguredPackageReader,
    UnconfiguredPdfTextPort,
    UnconfiguredPlanPort,
    UnconfiguredSourceRegistryPort,
    UnconfiguredTrackerRowsPort,
)
from gate.verdict import (
    config_hash,
    make_defect_id,
    order_defects,
    package_hash,
    verdict_for,
)

CHECK_NAMES = {
    GateStep.INTAKE: "package_intake",
    GateStep.RENDER: "word_to_pdf",
    GateStep.ROWCHECK: "rowcheck",
    GateStep.DOC_CHECKS: "doc_checks",
    GateStep.THIRD_PARTY_SCAN: "third_party_scan",
    GateStep.SANITISER_CHECK: "tool_names_stripped_verification",
}


class FixedClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 10, 4, tzinfo=UTC)

    def now(self) -> datetime:
        value = self.value
        self.value += timedelta(seconds=1)
        return value


class Reader:
    def __init__(self, snapshot: PackageSnapshot) -> None:
        self.snapshot = snapshot

    async def read_for_run(self, tenant_id, run_id) -> PackageSnapshot:
        del tenant_id, run_id
        return self.snapshot


class Checks:
    def __init__(self, status: CheckStatus = CheckStatus.PASSED) -> None:
        self.status = status
        self.calls: list[tuple[GateStep, PackageFile | None]] = []

    async def run(self, *, step, snapshot, target) -> CheckResult:
        del snapshot
        self.calls.append((step, target))
        return CheckResult(step, CHECK_NAMES[step], self.status)


class Store:
    def __init__(self) -> None:
        self.results: list[GateResult] = []

    async def insert_result(self, result: GateResult) -> None:
        self.results.append(result)


def snapshot(files: tuple[PackageFile, ...] = ()) -> PackageSnapshot:
    return PackageSnapshot(
        tenant_id=uuid4(),
        run_id=uuid4(),
        manifest=BuildManifest("package-1", files, (), ()),
    )


def pipeline(
    package: PackageSnapshot | None = None,
    *,
    checks=None,
    store=None,
    max_defects: int = 20,
) -> tuple[GatePipeline, PackageSnapshot, Checks, Store]:
    package = package or snapshot()
    check_adapter = checks or Checks()
    result_store = store or Store()
    runner = GatePipeline(
        package_reader=Reader(package),
        checks=check_adapter,
        gate_store=result_store,
        clock=FixedClock(),
        config_bytes=b"gate-config",
        max_defects=max_defects,
        runner_name="unit-test",
    )
    return runner, package, check_adapter, result_store


def test_package_and_config_hashes_are_deterministic_and_bind_exact_bytes() -> None:
    files = (("b.txt", b"second"), ("a.txt", b"first"))
    assert package_hash(files) == package_hash(tuple(reversed(files)))
    assert package_hash(files) != package_hash((("a.txt", b"firstsecond"),))
    assert package_hash(files) != package_hash((("a.txt", b"first"), ("c.txt", b"second")))
    assert config_hash(b"config") == config_hash(b"config")
    assert config_hash(b"config") != config_hash(b"config ")


def test_defect_id_is_stable_and_scoped_to_check_identity() -> None:
    identity = ("check_failed", "S2", "report.pdf", "row-4")
    assert make_defect_id(*identity) == make_defect_id(*identity)
    assert make_defect_id(*identity) != make_defect_id("check_failed", "S2", "other.pdf", "row-4")


def test_verdict_requires_at_least_one_successful_check_and_all_passed() -> None:
    passed = CheckResult(GateStep.INTAKE, "intake", CheckStatus.PASSED)
    failed = CheckResult(GateStep.INTAKE, "intake", CheckStatus.FAILED)
    not_run = CheckResult(GateStep.INTAKE, "intake", CheckStatus.NOT_RUN)
    assert verdict_for(()) is Verdict.FAIL
    assert verdict_for((passed,)) is Verdict.PASS
    assert verdict_for((passed, failed)) is Verdict.FAIL
    assert verdict_for((not_run,)) is Verdict.FAIL


def test_defects_are_sorted_capped_and_report_truncation() -> None:
    later = Defect("b", "z", "b.pdf", None, "z", "", None, GateStep.ROWCHECK)
    earlier = Defect("a", "a", "a.pdf", None, "a", "", None, GateStep.INTAKE)
    ordered, truncated = order_defects((later, earlier), 1)
    assert ordered == (earlier,)
    assert truncated
    assert order_defects((earlier,), 1) == ((earlier,), False)
    with pytest.raises(ValueError, match="defect limit must be positive"):
        order_defects((earlier,), 0)


def test_pipeline_passes_in_stable_check_and_target_order_and_persists_hashes() -> None:
    files = (
        PackageFile("z-report.PDF", b"pdf"),
        PackageFile("bundle.zip", b"zip"),
        PackageFile("b.xlsx", b"xlsx"),
        PackageFile("notes.txt", b"text"),
        PackageFile("a.docx", b"docx"),
    )
    package = snapshot(files)
    runner, _, checks, store = pipeline(package)

    result = asyncio.run(runner.run(package.tenant_id, package.run_id))

    assert result.verdict is Verdict.PASS
    assert result.package_hash == package_hash((item.name, item.content) for item in files)
    assert result.config_hash == config_hash(b"gate-config")
    assert [step for step, _ in checks.calls] == [
        GateStep.INTAKE,
        GateStep.RENDER,
        GateStep.ROWCHECK,
        GateStep.DOC_CHECKS,
        GateStep.DOC_CHECKS,
        GateStep.THIRD_PARTY_SCAN,
        GateStep.THIRD_PARTY_SCAN,
        GateStep.THIRD_PARTY_SCAN,
        GateStep.THIRD_PARTY_SCAN,
        GateStep.SANITISER_CHECK,
        GateStep.SANITISER_CHECK,
        GateStep.SANITISER_CHECK,
    ]
    assert [target.name if target else None for _, target in checks.calls] == [
        None,
        "a.docx",
        "z-report.PDF",
        "a.docx",
        "b.xlsx",
        "a.docx",
        "b.xlsx",
        "bundle.zip",
        "z-report.PDF",
        "a.docx",
        "b.xlsx",
        "bundle.zip",
    ]
    assert store.results == [result]
    assert result.finished_at > result.started_at


def test_pipeline_without_matching_files_still_runs_each_global_check() -> None:
    package = snapshot((PackageFile("readme.txt", b"text"),))
    runner, _, checks, store = pipeline(package)

    result = asyncio.run(runner.run(package.tenant_id, package.run_id))

    assert result.verdict is Verdict.PASS
    assert len(checks.calls) == len(GateStep)
    assert all(target is None for _, target in checks.calls)
    assert store.results == [result]


def test_pipeline_fails_closed_and_bounds_defects_for_failed_checks() -> None:
    package = snapshot()
    runner, _, _, _ = pipeline(
        package,
        checks=Checks(CheckStatus.FAILED),
        max_defects=2,
    )

    result = asyncio.run(runner.run(package.tenant_id, package.run_id))

    assert result.verdict is Verdict.FAIL
    assert len(result.checks) == len(GateStep)
    assert len(result.defects) == 2
    assert result.truncated


def test_pipeline_records_unconfigured_checks_as_not_run_and_fails() -> None:
    package = snapshot()
    runner, _, _, store = pipeline(package, checks=UnconfiguredGateCheckPort())

    result = asyncio.run(runner.run(package.tenant_id, package.run_id))

    assert result.verdict is Verdict.FAIL
    assert all(check.status is CheckStatus.NOT_RUN for check in result.checks)
    assert all(
        check.reason == "not_configured:legacy quality check adapters" for check in result.checks
    )
    assert len(result.defects) == len(GateStep)
    assert store.results == [result]


def test_pipeline_rejects_snapshot_outside_request_scope() -> None:
    package = snapshot()
    runner, _, checks, store = pipeline(package)
    with pytest.raises(ValueError, match="outside the request scope"):
        asyncio.run(runner.run(uuid4(), package.run_id))
    assert checks.calls == []
    assert store.results == []


def test_pipeline_rejects_mismatched_check_result() -> None:
    package = snapshot()

    class MismatchedChecks:
        async def run(self, *, step, snapshot, target) -> CheckResult:
            del step, snapshot, target
            return CheckResult(GateStep.ROWCHECK, "wrong_check", CheckStatus.PASSED)

    runner, _, _, store = pipeline(package, checks=MismatchedChecks())
    with pytest.raises(ValueError, match="mismatched result"):
        asyncio.run(runner.run(package.tenant_id, package.run_id))
    assert store.results == []


@pytest.mark.parametrize(
    ("config_bytes", "max_defects", "runner_name", "message"),
    [
        (b"config", 0, "test", "max_defects must be positive"),
        (b"", 1, "test", "configuration cannot be empty"),
        (b"config", 1, "   ", "runner name cannot be empty"),
    ],
)
def test_pipeline_rejects_invalid_configuration(
    config_bytes: bytes,
    max_defects: int,
    runner_name: str,
    message: str,
) -> None:
    package = snapshot()
    with pytest.raises(ValueError, match=message):
        GatePipeline(
            package_reader=Reader(package),
            checks=Checks(),
            gate_store=Store(),
            clock=FixedClock(),
            config_bytes=config_bytes,
            max_defects=max_defects,
            runner_name=runner_name,
        )


def test_pipeline_does_not_hide_check_or_store_failures() -> None:
    package = snapshot()

    class BrokenChecks:
        async def run(self, *, step, snapshot, target) -> CheckResult:
            del step, snapshot, target
            raise GateCheckError("adapter failed")

    runner, _, _, store = pipeline(package, checks=BrokenChecks())
    with pytest.raises(GateCheckError, match="adapter failed"):
        asyncio.run(runner.run(package.tenant_id, package.run_id))
    assert store.results == []

    class BrokenStore:
        async def insert_result(self, result: GateResult) -> None:
            del result
            raise OSError("store failed")

    runner, _, _, _ = pipeline(package, store=BrokenStore())
    with pytest.raises(OSError, match="store failed"):
        asyncio.run(runner.run(package.tenant_id, package.run_id))


def test_unconfigured_adapters_raise_explicit_errors() -> None:
    package = snapshot()
    file = PackageFile("source.docx", b"document")
    result = GateResult(
        tenant_id=package.tenant_id,
        run_id=package.run_id,
        package_hash="package-hash",
        config_hash="config-hash",
        verdict=Verdict.FAIL,
        checks=(),
        defects=(),
        truncated=False,
        started_at=datetime(2026, 10, 4, tzinfo=UTC),
        finished_at=datetime(2026, 10, 4, tzinfo=UTC),
        runner="unit-test",
    )
    adapters_and_calls = (
        (UnconfiguredPackageReader(), "read_for_run", (package.tenant_id, package.run_id)),
        (
            UnconfiguredGateCheckPort(),
            "run",
            (),
            {"step": GateStep.INTAKE, "snapshot": package, "target": None},
        ),
        (UnconfiguredGateStore(), "insert_result", (result,)),
        (UnconfiguredTrackerRowsPort(), "rows_for_run", (package.tenant_id, package.run_id)),
        (UnconfiguredTrackerRowsPort(), "tally_from_rows", ((),)),
        (UnconfiguredNumberRegistryPort(), "numbers_for_run", (package.tenant_id, package.run_id)),
        (UnconfiguredSourceRegistryPort(), "sources_for_run", (package.tenant_id, package.run_id)),
        (UnconfiguredExcludedPeoplePort(), "names_for_run", (package.tenant_id, package.run_id)),
        (
            UnconfiguredPlanPort(),
            "open_rows_closable_within_cap",
            (package.tenant_id, package.run_id),
        ),
        (UnconfiguredConverterPort(), "render_docx", (file,)),
        (UnconfiguredConverterPort(), "recalculate_workbook", (file,)),
        (UnconfiguredPdfTextPort(), "page_text", (file,)),
    )

    async def exercise() -> None:
        for adapter, method, args, *kwargs in adapters_and_calls:
            with pytest.raises(NotConfigured):
                await getattr(adapter, method)(*args, **(kwargs[0] if kwargs else {}))

    asyncio.run(exercise())
    with pytest.raises(NotConfigured, match="gate clock is not configured"):
        UnconfiguredClock().now()

    assert StaticBuildManifest.manifest_type is BuildManifest
    assert str(NotConfigured("adapter")) == "adapter is not configured"
    assert GateInputError.__name__ == "GateInputError"
    assert GateCheckError.__name__ == "GateCheckError"
