"""Steps 6 to 8 on the platform (code only): the export builder, the quality gate, the delivery
note, and a wallet that leaves settlement to the runner.

* ``ExportBuilderPort`` builds the standing client file set once per run with the export builder
  (which leak-scans every member itself, B9) and hands back each file's member texts for the
  agent layer's own scan (G9: provider codes and the person-name digest as well). A run without a
  wired builder gets the platform one (python-docx renderer), so no run ends
  ``builders_not_configured``. The internal audience builds the run's Internal Only file
  (``documents.internal_doc``: the cover and every page say "Internal Only", with the run's file
  findings and its coverage tracker) into its own location ``tenants/<t>/internal/<run>/``,
  recorded in ``internal_deliverables``; it is never a client deliverable and never scanned as
  one. Its id is ``internal:<object key>``.
* ``GateAdapter`` is the quality gate's ``check``.
* ``NoteDelivery`` writes the leak-scanned delivery note through the existing ``deliver`` stage
  (one implementation). Nothing is sent to anyone.
* ``RunnerSettles`` is the wallet: ``lifecycle.finish`` settles once at the terminal state.

GUARANTEE(test_builder_adapter_reads_member_texts_for_the_name_scan)
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.workflow.deps import RunDeps
from agents.ports import RunContext

_TEXT = (".xml", ".rels", ".json", ".md", ".txt", ".csv")


def member_texts(data: bytes) -> tuple[str, ...]:
    """Every text member of an OOXML or zip package, nested packages included, and the names."""
    out: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            out.append(name)
            body = z.read(name)
            if name.endswith((".docx", ".xlsx", ".zip")):
                out += member_texts(body)
            elif name.endswith(_TEXT):
                out.append(body.decode("utf-8", "ignore"))
    return tuple(out)


@dataclass
class ExportBuilderPort:
    """``BuilderPort`` on the export builder."""

    deps: RunDeps
    _built: dict[str, tuple[str, ...]] = field(default_factory=dict)

    async def build(self, ctx: RunContext, audience: str) -> tuple[str, ...]:
        """The client file set (built once), or the run's Internal Only file."""
        if audience != "client":
            return (await self._internal(ctx),)
        key = str(ctx.run_id)
        if key not in self._built:
            builder = self.deps.builder or _platform_builder(self.deps)
            ids = await builder.build(str(ctx.tenant_id), str(ctx.run_id))
            self._built[key] = tuple(str(i) for i in ids)
        return self._built[key]

    async def _internal(self, ctx: RunContext) -> str:
        """Build and store the Internal Only file once; its id."""
        import hashlib
        from datetime import date

        from documents import repo
        from documents.gate import INTERNAL_LABEL
        from documents.graph import findings, tracker_lines
        from documents.internal_doc import build

        async with self.deps.txn(ctx.tenant_id) as s:
            existing = await s.scalar(
                text("SELECT object_key FROM internal_deliverables WHERE run_id = :r"),
                {"r": ctx.run_id},
            )
            if existing is not None:
                return f"internal:{existing}"
            docs = await repo.run_documents(s, ctx.run_id)
            found = await findings(s, ctx.run_id)
            lines = await tracker_lines(s, ctx.run_id)
        label = INTERNAL_LABEL if docs else COMPANY_INTERNAL_LABEL
        data = build(
            account=ctx.account_name,
            as_of=date.fromisoformat(self.deps.clock.now().date().isoformat()),
            documents=[(d.name, int(d.passages)) for d in docs],
            findings=found,
            tracker=lines,
            label=label,
        )
        sha = hashlib.sha256(data).hexdigest()
        key = f"tenants/{ctx.tenant_id}/internal/{ctx.run_id}/{sha}.docx"
        await self.deps.payloads.put(key, data)
        async with self.deps.txn(ctx.tenant_id) as s:
            await repo.store_internal(
                s, ctx.run_id, self.deps.account_id, label, key, sha, len(data)
            )
        return f"internal:{key}"

    async def texts(self, ctx: RunContext, file_id: str) -> tuple[str, ...]:
        """The stored file's member texts (refused for another tenant's file by RLS)."""
        async with self.deps.txn(ctx.tenant_id) as s:
            key = (
                await s.execute(
                    text("SELECT object_key FROM deliverables WHERE id = :d"), {"d": UUID(file_id)}
                )
            ).scalar_one_or_none()
        if key is None:
            raise KeyError("deliverable not found for this tenant")
        data = await self.deps.payloads.get(str(key))
        try:
            return member_texts(data)
        except zipfile.BadZipFile:
            return (data.decode("utf-8", "ignore"),)


# The label of a company research run's Internal Only file (no uploaded material behind it).
COMPANY_INTERNAL_LABEL = "Internal: from this research run"


def _platform_builder(deps: RunDeps) -> Any:
    """The platform's export builder (python-docx renderer) for a run wired without one."""
    import os

    from exports.wiring import build_export_builder

    return build_export_builder(deps.payloads, deps.clock, os.environ, "python-interim")


@dataclass(frozen=True)
class GateAdapter:
    """``GatePort`` on the quality gate (no gate configured: one failing check, fail closed)."""

    deps: RunDeps

    async def check(self, ctx: RunContext) -> Mapping[str, list[str]]:
        """Failing checks by name."""
        if self.deps.gate is None:
            return {"gate_not_configured": ["quality gate"]}
        failing: Mapping[str, list[str]] = await self.deps.gate.check(
            str(ctx.tenant_id), str(ctx.run_id)
        )
        return {k: list(v) for k, v in failing.items() if v}


@dataclass(frozen=True)
class NoteDelivery:
    """``DeliveryPort``: the delivery note of the existing ``deliver`` stage."""

    deps: RunDeps

    async def release(
        self, ctx: RunContext, file_ids: Sequence[str], withheld: Sequence[str] = ()
    ) -> None:
        """Write the delivery note once (leak-scanned); files are already stored by the builder.
        A file kind the gate held back is recorded in the note; the workspace listing reads the
        stored gate verdict and leaves such a file out (``replica.workspace.deliverables``)."""
        from account_intelligence.workflow.stages.core import deliver

        state: Any = {
            "references": {
                "build": "built" if file_ids else "unknown",
                "gate": "passed" if not withheld else "withheld:" + ",".join(withheld),
                "withheld": list(withheld),
            },
            "counters": {},
        }
        await deliver(self.deps, state)


@dataclass(frozen=True)
class RunnerSettles:
    """``WalletPort``: settlement happens once in ``lifecycle.finish`` at the terminal state."""

    async def settle_once(self, ctx: RunContext, platform_fault: bool) -> bool:
        """No money moves here (the runner's finish settles exactly once)."""
        return False
