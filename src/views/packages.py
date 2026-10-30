"""Export packages (VW1, VW5): every file a tenant downloads is built from the same rows the
tables show, checked against them, scanned, and only then delivered.

* ``files``: the standing set (Account Summary and Sources and Evidence in Word; People, Job
  postings and Tech stack in Excel; the workspace zip) built by the export builders (which run
  their own self-checks: the client-output scan, row conservation, the PDF and recalculation
  checks), then compared with the materialised rows table by table (``views.parity``).
* ``csv``: one table as CSV, written from the materialised rows with every cell neutralised
  (``views.render_safe``), scanned, and read back for parity.
* ``pdf``: the Account Summary converted to PDF; the Facts by grade and Coverage tracker row keys
  must each appear once and in order in the PDF's text.

A mismatch, a refused build, withheld tables, a scan hit or a missing converter BLOCKS the
package (status ``blocked`` with a reason) and no download link is issued for it.

GUARANTEE(test_exported_rows_equal_api_rows_and_a_mutation_is_caught)
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.safety.client_output import ClientOutputLeak, assert_client_safe
from agents.errors import GuardViolation
from exports.verify import keys_once_in_order
from tenancy.storage import tenant_path
from views.parity import ParityResult, check, check_package
from views.render_safe import csv_cell
from views.tables import TABLES, TableSpec, specs

EXT = {
    "summary": "docx",
    "evidence": "docx",
    "people": "xlsx",
    "jobs": "xlsx",
    "tech": "xlsx",
    "workspace": "zip",
}


@dataclass
class PackageFile:
    """One file of a package: what it is, where it is, its hash."""

    kind: str
    name: str
    key: str
    sha256: str
    size: int
    store: str  # "deliverables" (the export store) or "objects" (the tenant object store)


@dataclass
class Package:
    """A package before it is recorded."""

    kind: str
    status: str
    reason: str | None
    files: list[PackageFile] = field(default_factory=list)
    parity: dict[str, ParityResult] = field(default_factory=dict)

    def parity_json(self) -> dict[str, Any]:
        """The parity verdicts as JSON (counts and problems; never cell content)."""
        return {
            k: {"ok": r.ok, "compared": r.compared, "problems": len(r.problems)}
            for k, r in self.parity.items()
        }


def blocked(kind: str, reason: str) -> Package:
    """A package that must not be delivered."""
    return Package(kind, "blocked", reason)


def verdict(pkg: Package) -> Package:
    """Delivered only when every parity verdict is clean."""
    if any(not r.ok for r in pkg.parity.values()):
        pkg.status, pkg.reason = "blocked", "parity_mismatch"
    else:
        pkg.status, pkg.reason = "delivered", None
    return pkg


def files_package(
    deliverables: list[tuple[str, str, str, bytes]],
    api: Mapping[str, list[dict[str, Any]]],
    withheld: Collection[str] = (),
) -> Package:
    """The standing set: ``deliverables`` are (kind, filename, object key, bytes). ``withheld``
    names the file kinds the builder left out of the set (``BuildResult.withheld_files``): the
    package carries the others, and the tables of a left out file are not compared with it."""
    pkg = Package("files", "blocked", None)
    produced: dict[str, tuple[str, bytes]] = {}
    from agents.naming import assert_client_clean
    from documents.gate import client_text

    for kind, name, key, body in deliverables:
        try:
            assert_client_clean((client_text(body),))
        except GuardViolation:
            return blocked("files", "client_output_refused")
        pkg.files.append(
            PackageFile(
                kind, name, key, hashlib.sha256(body).hexdigest(), len(body), "deliverables"
            )
        )
        if kind in ("summary", "people", "jobs", "tech"):
            produced[kind] = (EXT[kind], body)
    table_specs: dict[str, TableSpec] = {str(k): v for k, v in specs().items()}
    pkg.parity = check_package(table_specs, api, produced, withheld)
    return verdict(pkg)


def csv_bytes(spec: TableSpec, rows: list[dict[str, Any]]) -> bytes:
    """One table as CSV from its rows (exported columns, neutralised cells)."""
    from agents.naming import sanitize_client_value

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    cols = spec.exported_columns
    writer.writerow([sanitize_client_value(c.header) for c in cols])
    for row in rows:
        writer.writerow([csv_cell(sanitize_client_value(row.get(c.key))) for c in cols])
    return out.getvalue().encode("utf-8")


def csv_package(
    tenant_id: UUID, run_id: UUID, table: str, rows: list[dict[str, Any]]
) -> tuple[Package, bytes | None]:
    """A CSV of one table, scanned and read back for parity; returns the package and bytes."""
    spec = specs()[table]  # type: ignore[index]
    try:
        body = csv_bytes(spec, rows)
        assert_client_safe(body.decode("utf-8").splitlines())
    except (ClientOutputLeak, GuardViolation):
        return blocked("csv", "client_output_refused"), None
    digest = hashlib.sha256(body).hexdigest()
    key = tenant_path(tenant_id, "exports", str(run_id), f"{digest}.csv")
    pkg = Package(
        "csv",
        "blocked",
        None,
        [PackageFile(table, f"{table}.csv", key, digest, len(body), "objects")],
    )
    pkg.parity = {table: check(spec, rows, "csv", body)}
    return verdict(pkg), body


def pdf_package(
    tenant_id: UUID,
    run_id: UUID,
    pdf: bytes | None,
    pdf_text: str | None,
    api: Mapping[str, list[dict[str, Any]]],
) -> Package:
    """The Account Summary as PDF; parity is the facts and coverage row keys, once and in order."""
    if pdf is None or pdf_text is None:
        return blocked("pdf", "pdf_not_configured")
    from agents.naming import assert_client_clean

    try:
        assert_client_clean((pdf_text,))
    except GuardViolation:
        return blocked("pdf", "client_output_refused")
    digest = hashlib.sha256(pdf).hexdigest()
    key = tenant_path(tenant_id, "exports", str(run_id), f"{digest}.pdf")
    pkg = Package(
        "pdf",
        "blocked",
        None,
        [PackageFile("summary_pdf", "Account_Summary.pdf", key, digest, len(pdf), "objects")],
    )
    for table in ("facts", "coverage"):
        refs = [str(r.get("ref")) for r in api.get(table, [])]
        problems = keys_once_in_order(pdf_text, refs)
        pkg.parity[table] = ParityResult(table, len(refs), tuple(problems[:200]))
    return verdict(pkg)


async def record(
    s: AsyncSession,
    pkg: Package,
    *,
    run_id: UUID,
    account_id: UUID,
    actor: str,
    now: Any,
    rows_hash: str | None,
) -> UUID:
    """Insert the package (insert-only) in the caller's transaction; returns its id."""
    files = [f.__dict__ for f in pkg.files]
    pid: UUID = (
        await s.execute(
            text(
                "INSERT INTO export_packages (run_id, account_id, kind, status, reason, files,"
                " parity, rows_hash, requested_by, created_at) VALUES (:r, :a, :k, :st, :why,"
                " CAST(:f AS jsonb), CAST(:p AS jsonb), :h, :by, :now) RETURNING id"
            ),
            {
                "r": run_id,
                "a": account_id,
                "k": pkg.kind,
                "st": pkg.status,
                "why": pkg.reason,
                "f": json.dumps(files),
                "p": json.dumps(pkg.parity_json()),
                "h": rows_hash,
                "by": actor,
                "now": now,
            },
        )
    ).scalar_one()
    return pid


def all_tables() -> tuple[str, ...]:
    """Every client table (the API side of a files package)."""
    return TABLES
