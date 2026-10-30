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

Protections shared with the export build (``exports``): the run's content is read with the
tenant's CURRENT exclusions (``exports.readers.pg``); every rendered client file, the workspace
zip and the bundle document are scanned for the client-output denylist and the run's restricted
terms (``exports.verify.scan_package``, the set ``exports.readers.common.restricted_of``) as one
more gate check, and a hit fails the version; the version's manifest records the exclusion set it
was built with (``exclusion_hash``) so the routes hold back a version the tenant's exclusions
have since overtaken. The render runs OUTSIDE any database transaction (it takes minutes at
most; the version number is taken, the files stored and the row inserted in one short
transaction afterwards).

``build_from_run`` is what the graph's build node calls (``agents.production.parity_build``):
it reads the run's content through the export reader, the pursuing client's name from the
run's input or the tenant settings, the run's internal findings, and renders. ``ParityVersions``
is the read side the routes use.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
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

from obs.logging import logger
from parity.bundle import AccountBundle, dump_bundle, validate_bundle
from parity.from_run import RunFacts, apply_overrides, build_bundle_guarded
from parity.gate.__main__ import run_gate
from parity.gate.files import refused_name
from parity.gate.manifest import MANIFEST_NAME
from parity.gate.report import CheckResult, Finding
from parity.gate.report import result as gate_result
from parity.render import DOCX_CLI, render_workbooks

_log = logger("parity.service")
BUNDLE_NAME = "bundle.json"
STATUS_BUILT = "built"
STATUS_FAILED = "failed"
RENDER_TIMEOUT_S = 300.0
# the whole render (workbooks, Word files, the gate) may take this long; past it the build gives up
# (a recorded failed version) and the run goes on without a bundle
RENDER_BUDGET_S = 400.0
SCAN_NAME = "client_output_scan"


class ParityUnavailable(RuntimeError):
    """A tool the render needs is missing (LibreOffice, Node or the built Word renderer)."""


class ParityStale(RuntimeError):
    """The version was built under another exclusion set than the tenant's current one."""


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


def _failed_the_scan(gate: Mapping[str, Any]) -> bool:
    """Whether the gate report says the client-output scan failed."""
    return any(
        c.get("name") == SCAN_NAME and c.get("status") == "FAIL" for c in gate.get("checks", ())
    )


def scan_client_output(
    out_dir: Path, bundle: Mapping[str, Any], restricted: Sequence[str]
) -> CheckResult:
    """The export build's own leak scan over what this render produced: the client-output
    denylist (data tool names and hosts, credit counts, provider codes, internal hashes) and the
    run's restricted terms (``exports.readers.common.restricted_of``: the other accounts' names,
    the tenant's excluded companies, the names of unlisted people) in every client Word and Excel
    file, in the workspace zip and in the client view of the bundle document. Findings carry the
    file and a count, never the pattern or the text."""
    from exports.policy import leak_hits
    from exports.verify import scan_package
    from parity.gate.files import client_files

    findings: list[Finding] = []
    terms = tuple(restricted)
    scanned: set[str] = set()
    for path in client_files(out_dir):
        body = path.read_bytes()
        scanned.add(hashlib.sha256(body).hexdigest())
        hits = scan_package(body, terms)
        if hits:
            findings.append(Finding(SCAN_NAME, f"{len(hits)} forbidden term hit(s)", path.name))
    for path in sorted(out_dir.glob("*.zip")):
        if refused_name(path.name):
            continue
        hits = scan_package(path.read_bytes(), terms, scanned=frozenset(scanned))
        if hits:
            findings.append(Finding(SCAN_NAME, f"{len(hits)} forbidden term hit(s)", path.name))
    client_doc = json.loads(json.dumps(bundle, default=str))
    client_doc.get("meta", {}).pop("pursuing_client_internal", None)
    client_doc.get("prose", {}).pop("internal_only", None)
    bundle_hits = [
        h
        for h in leak_hits(json.dumps(client_doc, ensure_ascii=False), terms)
        if h != "internal_hash"
    ]
    if bundle_hits:
        findings.append(
            Finding(SCAN_NAME, f"{len(bundle_hits)} forbidden term hit(s)", BUNDLE_NAME)
        )
    return gate_result(SCAN_NAME, findings, {"terms": len(terms)})


def render_files(
    bundle: Mapping[str, Any],
    run_id: UUID,
    timeout_s: float = RENDER_TIMEOUT_S,
    restricted: Sequence[str] = (),
) -> RenderResult:
    """Render every file of a bundle, run the gate and return the bytes (synchronous; run it in
    a thread). Raises ``ParityUnavailable`` when a tool is missing. ``restricted`` is the run's
    restricted term set: a hit in a client file fails the gate."""
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
        report.checks.append(scan_client_output(out, doc, restricted))
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

    async def held_back(self, s: Any, found: BundleVersion) -> str | None:
        """Why a built version is not served, or None. The same protections as the delivered export
        files: a version built under another exclusion set than the tenant's current one is held
        back (the files and the bundle still hold what the tenant excluded since); so is one that
        lists a person erased after it was built, and one whose run's latest quality check failed
        (the export files of that run are held back too, so no part of the research shows twice with
        a different verdict). A later build of the account replaces it."""
        from exports.readers.exclusions import NO_EXCLUSIONS
        from views.materialize import current_exclusion_hash

        if found.status != "built":
            return None
        if str(
            found.manifest.get("exclusion_hash") or NO_EXCLUSIONS
        ) != await current_exclusion_hash(s):
            return "exclusions"
        erased = await s.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM people p WHERE p.account_id = :a"
                " AND p.erased_at IS NOT NULL AND p.erased_at > :t)"
            ),
            {"a": found.account_id, "t": found.created_at},
        )
        if erased:
            return "erasure"
        verdict = (
            await s.execute(
                text(
                    "SELECT g.verdict FROM run_gate_results g WHERE g.run_id = :r"
                    " ORDER BY g.created_at DESC, g.id DESC LIMIT 1"
                ),
                {"r": found.run_id},
            )
        ).first()
        # any failed verdict holds the whole version back, a file-level defect too: the export build
        # withholds only the file the defect names, but the bundle document carries every table in
        # one piece, so that file's rows would still be readable through it
        if verdict is not None and verdict.verdict == "fail":
            return "quality_check"
        return None

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
        """Read the run's content (under the tenant's current exclusions), build its bundle and
        render the account's next version."""
        if self.reader is None:
            raise ParityUnavailable("no content reader")
        content = await self.reader.read(str(tenant_id), str(run_id))
        done = await self.built_for_run(tenant_id, run_id, str(content.exclusion_hash or ""))
        if done is not None:
            # the build node ran again (a retry, a redelivery): the version stands, nothing is
            # rendered twice, and an analyst's later edit is not overwritten
            return done
        facts = await self.run_facts(tenant_id, run_id)
        bundle, restricted = build_bundle_guarded(content, facts)
        return await self.render_version(
            tenant_id,
            UUID(str(content.account_id)),
            run_id,
            bundle,
            restricted=restricted,
            exclusion_hash=str(content.exclusion_hash or ""),
        )

    async def built_for_run(
        self, tenant_id: UUID, run_id: UUID, exclusion_hash: str
    ) -> BundleVersion | None:
        """The run's newest built version when it was built under this exclusion set."""
        from exports.readers.exclusions import NO_EXCLUSIONS

        async with self.txn(tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT id, tenant_id, account_id, run_id, version, bundle_key,"
                        " manifest_json, gate_json, status, created_at FROM account_bundles"
                        " WHERE run_id = :r AND status = 'built' ORDER BY version DESC LIMIT 1"
                    ),
                    {"r": run_id},
                )
            ).first()
        if row is None:
            return None
        found = self._row(row)
        same = str(found.manifest.get("exclusion_hash") or NO_EXCLUSIONS) == (
            exclusion_hash or NO_EXCLUSIONS
        )
        return found if same else None

    async def edit(
        self,
        tenant_id: UUID,
        base: BundleVersion,
        overrides: Sequence[Mapping[str, Any]],
    ) -> BundleVersion:
        """Apply analyst corrections to a built version's bundle and render the next version.
        The run's content is read again under the tenant's current exclusions: a version the
        exclusions have overtaken is refused (``ParityStale``), and the render scans for the
        same restricted terms as a build."""
        from exports.readers.common import restricted_of
        from exports.readers.exclusions import NO_EXCLUSIONS

        if self.reader is None:
            raise ParityUnavailable("no content reader")
        content = await self.reader.read(str(tenant_id), str(base.run_id))
        current = str(content.exclusion_hash or NO_EXCLUSIONS)
        if str(base.manifest.get("exclusion_hash") or NO_EXCLUSIONS) != current:
            raise ParityStale("exclusions")
        from exports.config import default_export_config

        restricted = restricted_of(content, default_export_config().rules.attribution.name)
        bundle = apply_overrides(await self.bundle(base), overrides)
        return await self.render_version(
            tenant_id,
            base.account_id,
            base.run_id,
            bundle,
            restricted=restricted,
            exclusion_hash=current,
        )

    async def render_version(
        self,
        tenant_id: UUID,
        account_id: UUID,
        run_id: UUID,
        bundle: Mapping[str, Any],
        *,
        restricted: Sequence[str] = (),
        exclusion_hash: str = "",
    ) -> BundleVersion:
        """Render ``bundle`` as the account's next version, store its files and record it.
        The render runs before any transaction opens; only the version number, the stores and
        the row are inside one."""
        gate: dict[str, Any] = {}
        manifest: dict[str, Any] = {}
        status = STATUS_FAILED
        rendered: RenderResult | None = None
        try:
            rendered = await asyncio.wait_for(
                asyncio.to_thread(render_files, bundle, run_id, RENDER_TIMEOUT_S, restricted),
                timeout=RENDER_BUDGET_S,
            )
            gate, manifest = rendered.gate, rendered.manifest
            if rendered.passed:
                status = STATUS_BUILT
        except ParityUnavailable:
            raise
        except Exception as error:  # noqa: BLE001 - recorded as a failed version
            _log.warning(
                "parity.render_failed",
                extra={"run_id": str(run_id), "error_class": type(error).__name__},
            )
            gate = {"passed": False, "error": type(error).__name__, "message": str(error)[:400]}
            rendered = None
        if exclusion_hash:
            manifest = {**manifest, "exclusion_hash": exclusion_hash}
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
            if rendered is not None and status == STATUS_BUILT:
                for name, body in rendered.files.items():
                    await self.payloads.put(prefix + name, body)
            elif not _failed_the_scan(gate):
                # a failed version keeps only the bundle document (and nothing of it is served);
                # a version that failed the leak scan keeps nothing: the document holds the term
                document = (
                    rendered.files[BUNDLE_NAME]
                    if rendered is not None
                    else json.dumps(dict(bundle), ensure_ascii=False).encode()
                )
                await self.payloads.put(prefix + BUNDLE_NAME, document)
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
            extra={"run_id": str(run_id), "version": version, "state": status},
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
