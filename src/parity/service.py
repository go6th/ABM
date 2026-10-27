"""Bundle versions of an account: render, gate, store and record.

One call renders one version: the three workbooks (``parity.render``, recalculated with
LibreOffice), the Word files (the ``render/docx`` Node command, the internal-only file only when
the bundle carries ``prose.internal_only``), the workspace zip and the gate
(``parity.gate.run_gate``, in process). Every file, the bundle document and the manifest are
stored under ``tenants/<tenant>/accounts/<account>/runs/<run>/parity/v<version>/`` in the
payload store, and one row of ``account_bundles`` records the version: ``built`` when the gate
passed, ``failed`` with the gate report when it did not (then only the bundle document is
stored and nothing of the version is served). Versions count per account and are never
rewritten; a correction (``apply_overrides``) renders the next one.

``build_from_run`` is what the graph's build node calls (``agents.production.parity_build``):
it reads the run's content through the export reader, the pursuing client's name from the
run's input or the tenant settings, the run's internal findings, and renders. ``ParityVersions``
is the read side the routes use.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess  # nosec B404 - fixed argv, no shell
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text

from parity.bundle import AccountBundle, dump_bundle, validate_bundle
from parity.from_run import RunFacts, apply_overrides, build_bundle
from parity.gate.__main__ import run_gate
from parity.gate.files import refused_name
from parity.gate.manifest import MANIFEST_NAME
from parity.render import DOCX_CLI, render_workbooks

_log = logging.getLogger("parity.service")
BUNDLE_NAME = "bundle.json"
STATUS_BUILT = "built"
STATUS_FAILED = "failed"
RENDER_TIMEOUT_S = 900.0


class ParityUnavailable(RuntimeError):
    """A tool the render needs is missing (LibreOffice, Node or the built Word renderer)."""


@dataclass(frozen=True)
class BundleVersion:
    """One recorded version."""

    id: int
    tenant_id: UUID
    account_id: UUID
    run_id: UUID
    version: int
    bundle_key: str
    manifest: dict[str, Any]
    gate: dict[str, Any]
    status: str
    created_at: datetime

    @property
    def prefix(self) -> str:
        """The payload key prefix of the version's files."""
        return self.bundle_key[: -len(BUNDLE_NAME)]


@dataclass
class RenderResult:
    """What a render produced before storage: the files (name to bytes), the manifest, the
    gate report and whether it passed."""

    files: dict[str, bytes] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)
    gate: dict[str, Any] = field(default_factory=dict)
    passed: bool = False


def version_prefix(tenant_id: UUID, account_id: UUID, run_id: UUID, version: int) -> str:
    """``tenants/<t>/accounts/<a>/runs/<r>/parity/v<n>/``."""
    return f"tenants/{tenant_id}/accounts/{account_id}/runs/{run_id}/parity/v{version}/"


def docx_cli() -> Path:
    """The Word renderer command (``PARITY_DOCX_CLI`` or the repository's build)."""
    return Path(os.environ.get("PARITY_DOCX_CLI") or DOCX_CLI)


def tools_missing() -> list[str]:
    """The tools a render needs that this host lacks (empty when all are present)."""
    from parity.recalc import soffice_binary

    missing = []
    if soffice_binary() is None:
        missing.append("libreoffice")
    if shutil.which("node") is None:
        missing.append("node")
    if not docx_cli().exists():
        missing.append("render/docx")
    return missing


def _write_workspace(root: Path, bundle: Mapping[str, Any], run_id: UUID) -> Path:
    """A minimal workspace folder for the zip: the client-safe bundle document under ``build/``
    and a run note under ``notes/`` (the scrub replaces the pursuing client's name anyway)."""
    ws = root / "workspace"
    (ws / "build").mkdir(parents=True)
    (ws / "notes").mkdir(parents=True)
    client_doc = json.loads(json.dumps(bundle))
    client_doc["meta"].pop("pursuing_client_internal", None)
    client_doc["prose"].pop("internal_only", None)
    (ws / "build" / BUNDLE_NAME).write_text(
        json.dumps(client_doc, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    meta = bundle["meta"]
    (ws / "notes" / "run.md").write_text(
        f"# Run {run_id}\n\nAccount: {meta['account_short']} ({meta['domain']}).\n"
        f"Research date: {meta['run_date']}. {meta['byline']}\n",
        encoding="utf-8",
    )
    return ws


def render_files(
    bundle: Mapping[str, Any], run_id: UUID, timeout_s: float = RENDER_TIMEOUT_S
) -> RenderResult:
    """Render every file of a bundle, run the gate and return the bytes (synchronous; run it in
    a thread). Raises ``ParityUnavailable`` when a tool is missing."""
    missing = tools_missing()
    if missing:
        raise ParityUnavailable("missing: " + ", ".join(missing))
    model: AccountBundle = validate_bundle(bundle)
    doc = dump_bundle(model)
    result = RenderResult()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        out = root / "out"
        out.mkdir()
        bundle_path = root / BUNDLE_NAME
        bundle_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        render_workbooks(model, out, recalc=True)
        argv = [str(shutil.which("node")), str(docx_cli()), str(bundle_path), str(out)]
        if doc["prose"].get("internal_only"):
            argv.append("--internal")
        proc = subprocess.run(  # nosec B603 - fixed argv, no shell
            argv,
            capture_output=True,
            timeout=timeout_s,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", "LC_ALL": "C.UTF-8", "HOME": tmp},
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "word render failed: " + proc.stderr.decode("utf-8", "ignore")[-400:].strip()
            )
        workspace = _write_workspace(root, doc, run_id)
        report = run_gate(out, doc, workspace=workspace)
        result.gate = report.to_dict()
        result.manifest = dict(report.manifest or {})
        result.passed = report.passed
        for path in sorted(p for p in out.iterdir() if p.is_file()):
            result.files[path.name] = path.read_bytes()
        result.files[BUNDLE_NAME] = bundle_path.read_bytes()
    return result


class ParityVersions:
    """The read side: versions of an account and the stored bytes of a version."""

    def __init__(self, payloads: Any) -> None:
        self.payloads = payloads

    @staticmethod
    def _row(r: Any) -> BundleVersion:
        return BundleVersion(
            id=int(r.id),
            tenant_id=UUID(str(r.tenant_id)),
            account_id=UUID(str(r.account_id)),
            run_id=UUID(str(r.run_id)),
            version=int(r.version),
            bundle_key=str(r.bundle_key),
            manifest=dict(r.manifest_json or {}),
            gate=dict(r.gate_json or {}),
            status=str(r.status),
            created_at=r.created_at,
        )

    async def get(
        self, session: Any, account_id: UUID, version: int | None = None, *, built_only: bool = True
    ) -> BundleVersion | None:
        """The version asked for (the newest when None); only a built one unless told otherwise."""
        row = (
            await session.execute(
                text(
                    "SELECT id, tenant_id, account_id, run_id, version, bundle_key,"
                    " manifest_json, gate_json, status, created_at FROM account_bundles"
                    " WHERE account_id = :a AND (status = 'built' OR NOT :built_only)"
                    " AND (version = :v OR :v IS NULL) ORDER BY version DESC LIMIT 1"
                ),
                {"a": account_id, "v": version, "built_only": built_only},
            )
        ).first()
        return self._row(row) if row is not None else None

    async def list(self, session: Any, account_id: UUID) -> list[BundleVersion]:
        """Every version of the account, newest first."""
        rows = (
            await session.execute(
                text(
                    "SELECT id, tenant_id, account_id, run_id, version, bundle_key,"
                    " manifest_json, gate_json, status, created_at FROM account_bundles"
                    " WHERE account_id = :a ORDER BY version DESC"
                ),
                {"a": account_id},
            )
        ).all()
        return [self._row(r) for r in rows]

    async def bundle(self, found: BundleVersion) -> dict[str, Any]:
        """The stored bundle document of a version."""
        data: dict[str, Any] = json.loads(
            (await self.payloads.get(found.bundle_key)).decode("utf-8")
        )
        return data

    async def file(self, found: BundleVersion, name: str) -> tuple[bytes, dict[str, Any]]:
        """The stored bytes of one file of a built version with its manifest entry; ``KeyError``
        when the manifest does not list it."""
        entry = next((f for f in found.manifest.get("files", ()) if f.get("name") == name), None)
        if found.status != STATUS_BUILT or entry is None:
            raise KeyError(name)
        return await self.payloads.get(found.prefix + name), dict(entry)


class ParityService(ParityVersions):
    """The write side: render a bundle into the next version of its account."""

    def __init__(self, txn: Any, payloads: Any, clock: Any, reader: Any = None) -> None:
        super().__init__(payloads)
        self.txn = txn
        self.clock = clock
        self.reader = reader

    async def run_facts(self, tenant_id: UUID, run_id: UUID) -> RunFacts:
        """The pursuing client's name (the run's input, else the tenant settings) and the run's
        internal findings."""
        from documents.graph import findings
        from tenancy import settings as tenant_settings

        async with self.txn(tenant_id) as s:
            row = (
                await s.execute(
                    text("SELECT run_input FROM account_runs WHERE id = :r"), {"r": run_id}
                )
            ).first()
            run_input = dict(row.run_input or {}) if row else {}
            client = (
                run_input.get("pursuing_client")
                or (await tenant_settings.current(s)).settings.pursuing_client
            )
            found = await findings(s, run_id)
        items = tuple((f"{f.category} ({f.grade})", f"{f.value} [{f.citation}]") for f in found)
        return RunFacts(pursuing_client=str(client) if client else None, internal_items=items)

    async def build_from_run(self, tenant_id: UUID, run_id: UUID) -> BundleVersion:
        """Read the run's content, build its bundle and render the account's next version."""
        if self.reader is None:
            raise ParityUnavailable("no content reader")
        content = await self.reader.read(str(tenant_id), str(run_id))
        facts = await self.run_facts(tenant_id, run_id)
        bundle = build_bundle(content, facts)
        return await self.render_version(tenant_id, UUID(str(content.account_id)), run_id, bundle)

    async def edit(
        self,
        tenant_id: UUID,
        base: BundleVersion,
        overrides: Sequence[Mapping[str, Any]],
    ) -> BundleVersion:
        """Apply analyst corrections to a built version's bundle and render the next version."""
        bundle = apply_overrides(await self.bundle(base), overrides)
        return await self.render_version(tenant_id, base.account_id, base.run_id, bundle)

    async def render_version(
        self, tenant_id: UUID, account_id: UUID, run_id: UUID, bundle: Mapping[str, Any]
    ) -> BundleVersion:
        """Render ``bundle`` as the account's next version, store its files and record it."""
        async with self.txn(tenant_id) as s:
            await s.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                {"k": f"account_bundles:{account_id}"},
            )
            version = int(
                await s.scalar(
                    text(
                        "SELECT coalesce(max(version), 0) + 1 FROM account_bundles"
                        " WHERE account_id = :a"
                    ),
                    {"a": account_id},
                )
                or 1
            )
            prefix = version_prefix(tenant_id, account_id, run_id, version)
            gate: dict[str, Any] = {}
            manifest: dict[str, Any] = {}
            status = STATUS_FAILED
            try:
                rendered = await asyncio.to_thread(render_files, bundle, run_id)
                gate, manifest = rendered.gate, rendered.manifest
                if rendered.passed:
                    status = STATUS_BUILT
                    for name, body in rendered.files.items():
                        await self.payloads.put(prefix + name, body)
                else:
                    await self.payloads.put(prefix + BUNDLE_NAME, rendered.files[BUNDLE_NAME])
            except ParityUnavailable:
                raise
            except Exception as error:  # noqa: BLE001 - recorded as a failed version
                _log.warning(
                    "parity.render_failed",
                    extra={"run_id": str(run_id), "error": type(error).__name__},
                )
                gate = {"passed": False, "error": type(error).__name__, "message": str(error)[:400]}
                await self.payloads.put(
                    prefix + BUNDLE_NAME, json.dumps(dict(bundle), ensure_ascii=False).encode()
                )
            row = (
                await s.execute(
                    text(
                        "INSERT INTO account_bundles (tenant_id, owner_user_id, account_id, run_id,"
                        " version, bundle_key, manifest_json, gate_json, status)"
                        " VALUES (:t, coalesce((SELECT a.owner_user_id FROM accounts a"
                        " WHERE a.tenant_id = :t AND a.id = :a), app_user()), :a, :r, :v, :k,"
                        " CAST(:m AS jsonb), CAST(:g AS jsonb), :s)"
                        " RETURNING id, tenant_id, account_id, run_id, version, bundle_key,"
                        " manifest_json, gate_json, status, created_at"
                    ),
                    {
                        "t": tenant_id,
                        "a": account_id,
                        "r": run_id,
                        "v": version,
                        "k": prefix + BUNDLE_NAME,
                        "m": json.dumps(manifest, default=str),
                        "g": json.dumps(gate, default=str),
                        "s": status,
                    },
                )
            ).first()
        found = self._row(row)
        _log.info(
            "parity.version_recorded",
            extra={"run_id": str(run_id), "version": version, "status": status},
        )
        return found


def client_files(found: BundleVersion) -> list[dict[str, Any]]:
    """The manifest entries a client may receive: never a refused name."""
    return [
        f
        for f in found.manifest.get("files", ())
        if f.get("client", True)
        and not refused_name(str(f.get("name", "")))
        and f.get("name") not in (MANIFEST_NAME, BUNDLE_NAME)
    ]
