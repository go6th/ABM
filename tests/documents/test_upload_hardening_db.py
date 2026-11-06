"""Upload hardening, part B (2026-11-04): the size and CRC32C check at complete, the quarantine
store, scan_failed with bounded retries, and the daily abandoned-upload job. SYNTHETIC files."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pytest

from account_intelligence.db.session import SessionLocal
from documents.scan import StructureGuard
from documents.service import (
    SCAN_ATTEMPTS,
    DocumentService,
    mark_abandoned,
    quarantine_key,
    raw_key,
    retry_failed_scans,
)
from research_files.crc32c import crc32c_of_bytes
from tests.documents.world import admin, doc_row, service
from tests.fakes.scanner import DownScanner, FakeClamd

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
NOTES = b"notes about Northwind\n"


async def _send(svc: DocumentService, tenant: UUID, data: bytes, name: str = "notes.txt") -> UUID:
    """Start an upload and put its bytes through the token route; not yet completed."""
    started = await svc.start_upload(tenant, "user-1", name, len(data), None, None)
    token = parse_qs(urlparse(started["upload_url"]).query)["token"][0]

    async def body() -> object:
        yield data

    await svc.receive(tenant, UUID(started["document_id"]), token, body())  # type: ignore[arg-type]
    return UUID(started["document_id"])


def _scan_attempts(doc: UUID) -> int:
    return int(admin("SELECT scan_attempts FROM documents WHERE id = %s", doc)[0][0])


# ----------------------------------------------------------------------- complete: size and CRC32C


async def test_a_wrong_checksum_rejects_and_deletes_the_object(
    tenant_a: UUID, tmp_path: Path
) -> None:
    svc = service(tmp_path)
    doc = await _send(svc, tenant_a, NOTES)
    key = tmp_path / quarantine_key(tenant_a, doc)
    assert key.exists()
    done = await svc.complete(
        tenant_a, doc, declared_size=len(NOTES), declared_crc32c=crc32c_of_bytes(b"other bytes")
    )
    assert done["status"] == "rejected" and done["reason"] == "checksum_mismatch"
    assert "checksum" in done["message"]
    assert not key.exists() and svc.scheduler.documents == []


async def test_a_right_checksum_or_none_goes_on_to_the_scan(tenant_a: UUID, tmp_path: Path) -> None:
    svc = service(tmp_path)
    right = await _send(svc, tenant_a, NOTES)
    done = await svc.complete(
        tenant_a, right, declared_size=len(NOTES), declared_crc32c=crc32c_of_bytes(NOTES)
    )
    assert done["status"] == "quarantined"
    # an older browser sends no checksum: the checksum is skipped, the size still checked
    none = await _send(svc, tenant_a, NOTES)
    assert (await svc.complete(tenant_a, none))["status"] == "quarantined"
    assert svc.scheduler.documents == [(tenant_a, right), (tenant_a, none)]


async def test_a_wrong_declared_size_rejects_and_deletes_the_object(
    tenant_a: UUID, tmp_path: Path
) -> None:
    svc = service(tmp_path)
    doc = await _send(svc, tenant_a, NOTES)
    done = await svc.complete(
        tenant_a, doc, declared_size=len(NOTES) + 1, declared_crc32c=crc32c_of_bytes(NOTES)
    )
    assert done["status"] == "rejected" and done["reason"] == "size_mismatch"
    assert not (tmp_path / quarantine_key(tenant_a, doc)).exists()


# ----------------------------------------------------------------------- the quarantine store


async def test_the_quarantine_store_receives_the_upload_and_is_emptied_after_the_scan(
    tenant_a: UUID, tmp_path: Path
) -> None:
    docs, quarantine = tmp_path / "docs", tmp_path / "quarantine"
    svc = service(docs, quarantine=quarantine)
    doc = await _send(svc, tenant_a, NOTES)
    qkey = quarantine_key(tenant_a, doc)
    assert (quarantine / qkey).exists() and not (docs / qkey).exists()
    await svc.complete(
        tenant_a, doc, declared_size=len(NOTES), declared_crc32c=crc32c_of_bytes(NOTES)
    )
    assert await svc.process(tenant_a, doc) == "needs_account"  # read; no account given (PR5)
    sha = admin("SELECT sha256 FROM documents WHERE id = %s", doc)[0][0]
    assert (docs / raw_key(tenant_a, sha)).read_bytes() == NOTES
    assert not (quarantine / qkey).exists() and not (docs / qkey).exists()


async def test_a_document_created_before_the_bucket_is_read_from_the_documents_store(
    tenant_a: UUID, tmp_path: Path
) -> None:
    docs, quarantine = tmp_path / "docs", tmp_path / "quarantine"
    old = service(docs)  # no quarantine store: the old prefix of the documents store
    doc = await _send(old, tenant_a, NOTES)
    await old.complete(tenant_a, doc)
    assert (docs / quarantine_key(tenant_a, doc)).exists()
    new = service(docs, quarantine=quarantine)
    assert await new.process(tenant_a, doc) == "needs_account"
    assert not (docs / quarantine_key(tenant_a, doc)).exists()


async def test_a_mismatch_in_the_quarantine_store_deletes_it_there(
    tenant_a: UUID, tmp_path: Path
) -> None:
    svc = service(tmp_path / "docs", quarantine=tmp_path / "quarantine")
    doc = await _send(svc, tenant_a, NOTES)
    done = await svc.complete(tenant_a, doc, declared_crc32c=crc32c_of_bytes(b"x"))
    assert done["reason"] == "checksum_mismatch"
    assert not (tmp_path / "quarantine" / quarantine_key(tenant_a, doc)).exists()


# ----------------------------------------------------------------------- scan_failed


async def test_a_scanner_that_is_down_gives_scan_failed_and_the_file_is_never_read(
    tenant_a: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("a file that was not scanned must never reach the parser")

    monkeypatch.setattr("documents.service.parse_in_sandbox", boom)
    monkeypatch.setattr("documents.service.plan_in_sandbox", boom)
    svc = service(tmp_path, scanner=DownScanner())
    doc = await _send(svc, tenant_a, NOTES)
    await svc.complete(tenant_a, doc)
    assert await svc.process(tenant_a, doc) == "scan_failed"
    row = doc_row(doc)
    assert row["status"] == "scan_failed" and row["reject_reason"] == "scan_unavailable"
    assert (tmp_path / quarantine_key(tenant_a, doc)).exists()  # kept for the retry
    view = await svc.status(tenant_a, doc)
    assert view["stage"] == "failed" and "will be tried again" in view["message"]
    assert _scan_attempts(doc) == 1


async def test_the_sweep_stops_retrying_after_the_last_attempt(
    tenant_a: UUID, tmp_path: Path
) -> None:
    svc = service(tmp_path, scanner=DownScanner())
    doc = await _send(svc, tenant_a, NOTES)
    await svc.complete(tenant_a, doc)
    later = datetime.now(UTC) + timedelta(hours=1)
    for attempt in range(1, SCAN_ATTEMPTS + 1):
        assert await svc.process(tenant_a, doc) == "scan_failed"
        assert _scan_attempts(doc) == attempt
        retried = await retry_failed_scans(later, svc.scheduler, idle_s=60, tenant_id=tenant_a)
        assert retried == ([doc] if attempt < SCAN_ATTEMPTS else [])
    assert doc_row(doc)["status"] == "scan_failed"
    view = await svc.status(tenant_a, doc)
    assert view["message"].endswith("Upload it again later.")
    assert (tmp_path / quarantine_key(tenant_a, doc)).exists()


async def test_when_the_scanner_is_back_a_retry_scans_and_reads_the_file(
    tenant_a: UUID, tmp_path: Path
) -> None:
    svc = service(tmp_path, scanner=DownScanner())
    doc = await _send(svc, tenant_a, NOTES)
    await svc.complete(tenant_a, doc)
    assert await svc.process(tenant_a, doc) == "scan_failed"
    svc.scanner = StructureGuard(FakeClamd())
    # too soon after the last attempt: not yet
    assert (
        await retry_failed_scans(datetime.now(UTC), svc.scheduler, idle_s=3600, tenant_id=tenant_a)
        == []
    )
    later = datetime.now(UTC) + timedelta(hours=2)
    assert await retry_failed_scans(later, svc.scheduler, idle_s=3600, tenant_id=tenant_a) == [doc]
    assert doc_row(doc)["status"] == "quarantined" and doc_row(doc)["reject_reason"] is None
    assert await svc.process(tenant_a, doc) == "needs_account"
    assert _scan_attempts(doc) == 2


async def test_an_infected_file_is_rejected_not_scan_failed(tenant_a: UUID, tmp_path: Path) -> None:
    from documents.scan import EICAR

    svc = service(tmp_path)
    doc = await _send(svc, tenant_a, EICAR)
    await svc.complete(tenant_a, doc)
    assert await svc.process(tenant_a, doc) == "rejected"
    assert doc_row(doc)["status"] == "rejected" and doc_row(doc)["reject_reason"] == "infected"
    assert not (tmp_path / quarantine_key(tenant_a, doc)).exists()


# ----------------------------------------------------------------------- abandoned uploads


async def test_the_daily_job_abandons_old_awaiting_uploads_only(
    tenant_a: UUID, tenant_b: UUID, tmp_path: Path
) -> None:
    svc = service(tmp_path)
    old = await _send(svc, tenant_a, NOTES)  # bytes arrived, complete never called
    fresh = await _send(svc, tenant_a, NOTES)
    other = await _send(svc, tenant_b, NOTES)  # another client's old row
    admin(
        "UPDATE documents SET created_at = now() - interval '25 hours' WHERE id = ANY(%s)",
        [old, other],
    )
    now = datetime.now(UTC)
    assert await mark_abandoned(SessionLocal, now, service=svc, tenant_id=tenant_a) == 1
    assert doc_row(old)["status"] == "abandoned"
    assert doc_row(fresh)["status"] == "awaiting_upload"
    assert doc_row(other)["status"] == "awaiting_upload"  # a call scoped to tenant A leaves B alone
    assert not (tmp_path / quarantine_key(tenant_a, old)).exists()
    assert (tmp_path / quarantine_key(tenant_a, fresh)).exists()
    view = await svc.status(tenant_a, old)
    assert view["stage"] == "failed" and view["reason"] == "abandoned"
    # every client: the other client's old row is found by the overview and abandoned too
    assert await mark_abandoned(SessionLocal, now, service=svc) >= 1
    assert doc_row(other)["status"] == "abandoned" and doc_row(fresh)["status"] == "awaiting_upload"
    # idempotent
    assert await mark_abandoned(SessionLocal, now, service=svc, tenant_id=tenant_a) == 0
