"""The status of an import, per upload and per part (migration 0106, ingestion hardening PR2):
``import_runs`` / ``import_run_parts`` written by ``documents.import_status`` as the workspace
import runs, shown on the Uploaded files sheet and as the account page's ``import_problems``."""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text as sql_text

from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import sheets
from account_intelligence.replica.workspace import workspace
from tests.conftest import _make_account
from tests.documents import workspace_zip
from tests.documents.test_workspace_zip_import_db import F, _zip_with_twins
from tests.documents.world import admin, service, upload

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

JOBS_MEMBER = f"{F}/deliverables/{workspace_zip.JOBS_FILE}"


def _zip_without_links() -> bytes:
    """The workspace zip whose Job Postings workbook has no web address on any row."""
    plain = zipfile.ZipFile(io.BytesIO(workspace_zip.workspace_zip_bytes()))
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(_zip_with_twins())) as src,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z,
    ):
        for info in src.infolist():
            data = src.read(info)
            if info.filename.endswith(workspace_zip.JOBS_FILE):
                data = plain.read(info.filename)
            z.writestr(info, data)
    return out.getvalue()


def _parts(doc: UUID) -> dict[str, dict[str, Any]]:
    rows = admin(
        "SELECT p.part, p.status, p.rows_written, p.rows_skipped, p.skip_reasons,"
        " p.error_message, p.attempts, p.started_at IS NOT NULL, p.finished_at IS NOT NULL"
        " FROM import_run_parts p JOIN import_runs r ON r.id = p.import_run_id"
        " AND r.tenant_id = p.tenant_id WHERE r.document_id = %s",
        doc,
    )
    keys = ("status", "written", "skipped", "reasons", "error", "attempts", "started", "finished")
    return {str(r[0]): dict(zip(keys, r[1:], strict=True)) for r in rows}


async def _import(tmp_path: Path, tenant: UUID, data: bytes) -> tuple[UUID, UUID, Any]:
    account = _make_account(tenant, f"holdings-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    done = await upload(svc, tenant, workspace_zip.ZIP_NAME, data, account)
    doc = UUID(done["document_id"])
    assert await svc.process(tenant, doc) == "indexed"
    return account, doc, svc


async def _read(tenant: UUID, account: UUID) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    async with tenant_txn(tenant, user="user-1") as s:
        ws = await workspace(s, account, "user-1", datetime.now(UTC))
        found = await s.execute(
            sql_text(f"SELECT * FROM ({sheets.SHEETS['uploaded-files'].base}) s"),  # noqa: S608 allow-listed
            {"a": account, "u": "user-1", "now": datetime.now(UTC), "t": tenant},
        )
        return ws, [dict(r) for r in found.mappings().all()]


async def test_the_import_writes_one_run_and_five_parts(tenant_a: UUID, tmp_path: Path) -> None:
    account, doc, _ = await _import(tmp_path, tenant_a, _zip_with_twins())
    runs = admin(
        "SELECT status, attempts, account_id, file_import_run_id IS NOT NULL,"
        " finished_at IS NOT NULL"
        " FROM import_runs WHERE document_id = %s",
        doc,
    )
    assert runs == [("ok", 1, account, True, True)]
    parts = _parts(doc)
    assert set(parts) == {"jobs", "people", "tech", "docx", "raw"}
    assert {p["status"] for p in parts.values()} == {"ok"}
    for name in ("jobs", "people", "tech"):
        assert parts[name]["written"] > 0 and parts[name]["attempts"] == 1
        assert parts[name]["started"] and parts[name]["finished"]
    ws, files = await _read(tenant_a, account)
    assert ws["import_problems"] == []
    shown = {r["member"]: r["import_status"] for r in files}
    assert shown[JOBS_MEMBER] == "ok"
    # the rows are owned by the document's owner, so its uploader sees them
    assert admin(
        "SELECT count(*) FROM import_run_parts p JOIN import_runs r ON r.id = p.import_run_id"
        " AND r.tenant_id = p.tenant_id JOIN documents d ON d.id = r.document_id"
        " WHERE r.document_id = %s AND (p.owner_user_id <> d.owner_user_id"
        " OR r.owner_user_id <> d.owner_user_id)",
        doc,
    ) == [(0,)]


async def test_postings_without_a_link_are_kept_without_an_address(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """An uploaded sheet's archive records without a link are rows of the sheet, so they are rows
    of the table, with no address (migration 0111; exact copy, owner 2026-11-05). Before, the
    whole jobs part was skipped "no web address"."""
    account, doc, _ = await _import(tmp_path, tenant_a, _zip_without_links())
    parts = _parts(doc)
    jobs = parts["jobs"]
    assert jobs["status"] == "ok" and jobs["written"] > 0 and jobs["skipped"] == 0
    assert not any("no web address" in r for r in jobs["reasons"])
    assert all(parts[p]["status"] == "ok" for p in ("people", "tech", "docx"))
    assert admin("SELECT count(*) FROM people WHERE account_id = %s", account)[0][0] > 0
    stored = admin("SELECT url FROM job_postings WHERE account_id = %s", account)
    assert len(stored) == jobs["written"] and any(url is None for (url,) in stored)
    _, files = await _read(tenant_a, account)
    shown = {r["member"]: r["import_status"] for r in files}
    assert shown[JOBS_MEMBER].startswith("imported") or "skipped" not in shown[JOBS_MEMBER]


async def test_a_part_that_raises_is_failed_and_shown(
    tenant_a: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from documents import workspace_import

    async def boom(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("cell text that must not be stored")

    monkeypatch.setattr(workspace_import, "import_postings", boom)
    account, doc, _ = await _import(tmp_path, tenant_a, _zip_with_twins())
    parts = _parts(doc)
    assert parts["jobs"]["status"] == "failed" and parts["jobs"]["written"] == 0
    assert parts["jobs"]["error"].startswith("RuntimeError")
    assert len(parts["jobs"]["error"]) <= 300
    assert all(parts[p]["status"] == "ok" for p in ("people", "tech", "docx", "raw"))
    assert admin("SELECT status FROM import_runs WHERE document_id = %s", doc) == [("failed",)]
    # the other parts' rows landed although the first one failed
    assert admin("SELECT count(*) FROM people WHERE account_id = %s", account)[0][0] > 0
    ws, files = await _read(tenant_a, account)
    assert [(p["part"], p["status"], p["document_name"]) for p in ws["import_problems"]] == [
        ("jobs", "failed", workspace_zip.ZIP_NAME)
    ]
    assert "must not be stored" not in json.dumps(ws["import_problems"])
    shown = {r["member"]: r["import_status"] for r in files}
    assert shown[JOBS_MEMBER] == "failed"


async def test_a_second_import_reuses_the_run(tenant_a: UUID, tmp_path: Path) -> None:
    from documents.deliverables import backfill_imports

    account, doc, svc = await _import(tmp_path, tenant_a, _zip_with_twins())
    admin("UPDATE documents SET imported_at = NULL WHERE id = %s", doc)
    await backfill_imports(datetime.now(UTC), limit=10, payloads=svc.payloads, blobs=svc.blobs)
    assert admin("SELECT count(*), max(attempts) FROM import_runs WHERE document_id = %s", doc) == [
        (1, 2)
    ]
    parts = _parts(doc)
    assert len(parts) == 5 and parts["jobs"]["attempts"] == 2
    assert {p["status"] for p in parts.values()} == {"ok"}
    assert account


async def test_the_import_status_tables_are_private_to_the_tenant(
    tenant_a: UUID, tenant_b: UUID, tmp_path: Path
) -> None:
    _, doc, _ = await _import(tmp_path, tenant_a, _zip_with_twins())
    async with tenant_txn(tenant_b, user="user-1") as s:
        n = await s.scalar(sql_text("SELECT count(*) FROM import_runs"))
        m = await s.scalar(sql_text("SELECT count(*) FROM import_run_parts"))
    assert (n, m) == (0, 0)
    assert admin("SELECT count(*) FROM import_runs WHERE document_id = %s", doc) == [(1,)]


async def test_log_lines_inside_the_context_carry_its_ids() -> None:
    import io as _io

    from obs.catalog import load_catalog
    from obs.exporters import DropCounter
    from obs.logging import JsonFormatter, log_context

    buf = _io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter(load_catalog(), DropCounter(), "worker"))
    log = logging.getLogger("import-status-test")
    log.addHandler(handler)
    log.propagate = False
    log.setLevel(logging.INFO)
    ids = {k: str(uuid4()) for k in ("tenant_id", "account_id", "document_id", "import_run_id")}
    try:
        with log_context(**ids):
            with log_context(part="jobs"):
                log.info("inside_part")
            log.info("inside_run")
        log.info("outside")
    finally:
        log.removeHandler(handler)
    inside, run, outside = (json.loads(x) for x in buf.getvalue().splitlines())
    assert {k: inside[k] for k in (*ids, "part")} == {**ids, "part": "jobs"}
    assert {k: run[k] for k in ids} == ids and "part" not in run
    assert not any(k in outside for k in (*ids, "part"))


async def test_an_import_that_stops_before_its_parts_is_shown_as_failed(
    tenant_a: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review B1 (2026-11-04): a failure before any part was planned leaves a run without parts;
    the banner lists it (part ``import``) and every file that feeds a part shows "failed"."""
    from documents import workspace_docx

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("planner broke")

    monkeypatch.setattr(workspace_docx, "plan", boom)
    account, doc, _ = await _import(tmp_path, tenant_a, _zip_with_twins())
    assert admin("SELECT status, attempts FROM import_runs WHERE document_id = %s", doc) == [
        ("failed", 1)
    ]
    ws, files = await _read(tenant_a, account)
    assert [(p["part"], p["status"]) for p in ws["import_problems"]] == [("import", "failed")]
    assert ws["import_problems"][0]["reason"].startswith("RuntimeError")
    shown = {r["member"]: r["import_status"] for r in files}
    assert shown[JOBS_MEMBER] == "failed"


async def test_an_import_that_stops_between_parts_leaves_no_part_pending(
    tenant_a: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review B1: the file-import run could not be recorded after the parts were planned; the
    parts that never ran are failed (not "pending" under a finished run) and listed."""
    from account_intelligence.runs import lifecycle

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("run row refused")

    monkeypatch.setattr(lifecycle, "record_file_import_run", boom)
    account, doc, _ = await _import(tmp_path, tenant_a, _zip_with_twins())
    parts = _parts(doc)
    assert parts and all(p["status"] in ("failed", "skipped") for p in parts.values()), parts
    assert not any(p["status"] in ("pending", "running") for p in parts.values())
    ws, _ = await _read(tenant_a, account)
    assert ws["import_problems"] and all(p["status"] == "failed" for p in ws["import_problems"])
