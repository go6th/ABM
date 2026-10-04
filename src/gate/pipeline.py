"""Ordered, read-only, fail-closed quality-gate orchestration.

Guarantees: every configured check is attempted in stable order, missing legacy
adapters fail, and results bind to exact package/config hashes
(GUARANTEE(test_missing_legacy_checks_fail_closed)).
"""

from __future__ import annotations

from uuid import UUID

from gate.errors import NotConfigured
from gate.models import (
    CheckResult,
    CheckStatus,
    Defect,
    GateResult,
    GateStep,
    PackageFile,
)
from gate.ports import Clock, GateCheckPort, GateStore, PackageReader
from gate.verdict import config_hash, make_defect_id, order_defects, package_hash, verdict_for

_CHECK_PLAN: tuple[tuple[GateStep, str], ...] = (
    (GateStep.INTAKE, "package_intake"),
    (GateStep.RENDER, "word_to_pdf"),
    (GateStep.ROWCHECK, "rowcheck"),
    (GateStep.DOC_CHECKS, "doc_checks"),
    (GateStep.THIRD_PARTY_SCAN, "third_party_scan"),
    (GateStep.SANITISER_CHECK, "tool_names_stripped_verification"),
)


class GatePipeline:
    """Execute the mandated ordered check plan and persist the immutable result."""

    def __init__(
        self,
        *,
        package_reader: PackageReader,
        checks: GateCheckPort,
        gate_store: GateStore,
        clock: Clock,
        config_bytes: bytes,
        max_defects: int,
        runner_name: str,
    ) -> None:
        if max_defects < 1:
            raise ValueError("max_defects must be positive")
        if not config_bytes:
            raise ValueError("gate configuration cannot be empty")
        if not runner_name.strip():
            raise ValueError("runner name cannot be empty")
        self._package_reader = package_reader
        self._checks = checks
        self._gate_store = gate_store
        self._clock = clock
        self._config_hash = config_hash(config_bytes)
        self._max_defects = max_defects
        self._runner_name = runner_name

    async def run(self, tenant_id: UUID, run_id: UUID) -> GateResult:
        """Run checks and store the result; no check or store failure can become PASS."""
        started = self._clock.now()
        snapshot = await self._package_reader.read_for_run(tenant_id, run_id)
        if snapshot.tenant_id != tenant_id or snapshot.run_id != run_id:
            raise ValueError("package reader returned a resource outside the request scope")

        file_hash = package_hash((item.name, item.content) for item in snapshot.manifest.files)
        checks: list[CheckResult] = []
        defects: list[Defect] = []
        for step, check_name in _CHECK_PLAN:
            targets = self._targets(step, snapshot.manifest.files)
            for target in targets or (None,):
                try:
                    check_result = await self._checks.run(
                        step=step,
                        snapshot=snapshot,
                        target=target,
                    )
                    if check_result.step is not step or check_result.check != check_name:
                        raise ValueError("check adapter returned a mismatched result")
                except NotConfigured as error:
                    check_result = CheckResult(
                        step=step,
                        check=check_name,
                        status=CheckStatus.NOT_RUN,
                        reason=f"not_configured:{error.dependency}",
                    )
                checks.append(check_result)
                if check_result.status is not CheckStatus.PASSED:
                    defects.append(
                        Defect(
                            defect_id=make_defect_id(
                                "check_not_passed",
                                step.value,
                                target.name if target is not None else None,
                                check_name,
                            ),
                            code="check_not_passed",
                            file=target.name if target is not None else None,
                            part=None,
                            location=check_name,
                            redacted_preview="A required delivery check did not pass.",
                            content_hash=None,
                            rerun_step=step,
                        )
                    )

        bounded_defects, truncated = order_defects(defects, self._max_defects)
        finish = self._clock.now()
        gate_result = GateResult(
            tenant_id=tenant_id,
            run_id=run_id,
            package_hash=file_hash,
            config_hash=self._config_hash,
            verdict=verdict_for(checks),
            checks=tuple(checks),
            defects=bounded_defects,
            truncated=truncated,
            started_at=started,
            finished_at=finish,
            runner=self._runner_name,
        )
        await self._gate_store.insert_result(gate_result)
        return gate_result

    @staticmethod
    def _targets(
        step: GateStep,
        files: tuple[PackageFile, ...],
    ) -> tuple[PackageFile, ...]:
        """Select inputs based on file format; actual checks remain adapter-owned."""
        suffixes = {
            GateStep.RENDER: {".docx"},
            GateStep.ROWCHECK: {".pdf"},
            GateStep.DOC_CHECKS: {".docx", ".xlsx"},
            GateStep.THIRD_PARTY_SCAN: {".docx", ".xlsx", ".zip", ".pdf"},
            GateStep.SANITISER_CHECK: {".docx", ".xlsx", ".zip"},
        }
        relevant = suffixes.get(step)
        if relevant is None:
            return ()
        return tuple(
            item
            for item in sorted(files, key=lambda value: value.name)
            if any(item.name.lower().endswith(suffix) for suffix in relevant)
        )
