"""The parity bundle build beside the export build (graph step 6, ``PARITY_BUNDLE=on``).

``ParityBuildPort.build`` reads the run's stored content through the export reader, builds the
AccountBundle (``parity.from_run``), renders and gates it and records the account's next
version (``parity.service``). It never decides the run's outcome: a failed gate is a failed
version (recorded with its report, nothing of it served) and a missing tool is logged; the
export path keeps delivering the legacy files either way.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from account_intelligence.workflow.deps import RunDeps
from agents.ports import RunContext

_log = logging.getLogger("agents.parity")


@dataclass
class ParityBuildPort:
    """``ParityPort`` on ``parity.service.ParityService``."""

    deps: RunDeps

    def _service(self) -> Any:
        from exports.config import default_export_config
        from exports.readers.pg import PgContentReader
        from parity.service import ParityService

        reader = getattr(self.deps.builder, "reader", None)
        if reader is None:
            cfg = default_export_config()
            reader = PgContentReader(self.deps.payloads, batch=cfg.rules.limits.keyset_batch)
        return ParityService(self.deps.txn, self.deps.payloads, self.deps.clock, reader=reader)

    async def build(self, ctx: RunContext) -> dict[str, Any]:
        """Build the run's bundle version; the outcome as counters (version, status) or the
        reason nothing was built (``skipped``)."""
        from parity.from_run import BundleIncomplete
        from parity.service import ParityUnavailable

        try:
            found = await self._service().build_from_run(ctx.tenant_id, ctx.run_id)
        except ParityUnavailable as missing:
            _log.warning("parity.skipped", extra={"run_id": str(ctx.run_id), "why": str(missing)})
            return {"skipped": str(missing)}
        except BundleIncomplete as incomplete:
            _log.warning(
                "parity.bundle_incomplete",
                extra={"run_id": str(ctx.run_id), "codes": str(incomplete)},
            )
            return {"skipped": f"bundle_incomplete:{incomplete}"}
        return {"version": found.version, "status": found.status}


def parity_port(deps: RunDeps, env: Any) -> ParityBuildPort | None:
    """The port when the switch is on, else None (the build node then skips the bundle)."""
    return ParityBuildPort(deps) if env.get("PARITY_BUNDLE") == "on" else None
