"""Uploaded documents on Postgres: scan, sandboxed parse, redaction, chunking, the private index,
rejections before parsing, company detection, deletion and re-evaluation, the orphan sweeper,
and tenant isolation. SYNTHETIC files only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from account_intelligence.db.session import SessionLocal, tenant_txn
from documents import repo
from documents.scan import UnavailableScanner
from documents.service import DocumentError, sweep_documents
from tests.conftest import _make_account
from tests.documents import fixtures
from tests.documents.world import admin, doc_row, service, upload

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _never_parse(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    called: list[str] = []

    def boom(*args: object, **kwargs: object) -> None:
        called.append("parsed")
        raise AssertionError("a rejected file must never reach the parser")

    monkeypatch.setattr("documents.service.parse_in_sandbox", boom)
    return called


@pytest.mark.parametrize(
    ("name", "make"),
    [
        ("call.pdf", fixtures.meeting_pdf),
        ("notes.docx", fixtures.notes_docx),
        ("crm.xlsx", fixtures.crm_xlsx),
    ],
)
async def test_file_upload_runs_through_scan_parse_and_index(
    name: str, make: object, tenant_a: UUID, tmp_path: Path
) -> None:
    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, name, make(), account)  # type: ignore[operator]
    assert done["status"] == "quarantined"
    doc = UUID(done["document_id"])
    assert svc.scheduler.documents == [(tenant_a, doc)]
    # nothing parsed yet: no passage exists before the scan
    assert admin("SELECT count(*) FROM document_passages WHERE document_id = %s", doc) == [(0,)]
    assert await svc.process(tenant_a, doc) == "indexed"
    row = doc_row(doc)
    assert row["status"] == "indexed" and row["passages"] >= 1 and row["account_id"] == account
    passages = admin(
        "SELECT kind, location, heading_path, account_id FROM document_passages"
        " WHERE document_id = %s ORDER BY seq",
        doc,
    )
    assert all(p[3] == account and p[1] and p[2] for p in passages)
    index = admin(
        "SELECT count(*), bool_and(visibility = 'private'), min(embedding_model),"
        " bool_and(account_id = %s) FROM chunks WHERE doc_id = %s",
        account,
        doc,
    )
    assert index == [(len(passages), True, "local-hash-v1", True)]
    # the raw object left quarantine; the embedding batch was metered
    assert not (tmp_path / f"quarantine/tenants/{tenant_a}/{doc}/raw").exists()
    assert (tmp_path / f"tenants/{tenant_a}/documents/{doc}/raw").exists()
    assert admin(
        "SELECT task, model, cost_usd FROM research_file_model_calls WHERE file_id = %s", doc
    )[0] == ("embed_passages", "local-hash-v1", 0)
    # S5: each logged embedding call belongs to the document's owner (the table's owner trigger
    # looks for a research file, which a document never has)
    owners = admin(
        "SELECT DISTINCT c.owner_user_id = d.owner_user_id FROM research_file_model_calls c"
        " JOIN documents d ON d.id = c.file_id WHERE c.file_id = %s",
        doc,
    )
    assert owners == [(True,)]
    kinds = {p[0] for p in passages}
    assert kinds == ({"crm_row"} if name.endswith(".xlsx") else {"section"})
    events = admin("SELECT action, outcome FROM audit_events WHERE resource_id = %s", str(doc))
    assert ("run.document.parse", "ok") in events


@pytest.mark.parametrize(
    ("name", "data", "reason"),
    [
        ("virus.txt", fixtures.eicar_txt(), "infected"),
        ("macro.docx", fixtures.macro_docx(), "infected"),
        ("report.pdf", fixtures.exe_as_pdf(), "wrong_type"),
        ("notes.docx", fixtures.meeting_pdf(), "wrong_type"),
    ],
)
async def test_infected_upload_is_rejected_before_parsing(
    name: str,
    data: bytes,
    reason: str,
    tenant_a: UUID,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = _never_parse(monkeypatch)
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, name, data)
    doc = UUID(done["document_id"])
    assert await svc.process(tenant_a, doc) == "rejected"
    row = doc_row(doc)
    assert (row["status"], row["reject_reason"]) == ("rejected", reason)
    assert called == [] and not (tmp_path / f"tenants/{tenant_a}/documents/{doc}/raw").exists()
    assert admin("SELECT count(*) FROM document_passages WHERE document_id = %s", doc) == [(0,)]
    view = await svc.status(tenant_a, doc)
    assert view["stage"] == "failed" and view["message"]


async def test_oversized_wrong_type_and_audio_are_refused_before_upload(
    tenant_a: UUID, tmp_path: Path
) -> None:
    svc = service(tmp_path)
    too_big = svc.c.upload.max_file_bytes + 1
    with pytest.raises(DocumentError) as big:
        await svc.start_upload(tenant_a, "u", "big.pdf", too_big, None, None)
    assert big.value.code == "too_large"
    with pytest.raises(DocumentError) as exe:
        await svc.start_upload(tenant_a, "u", "tool.exe", 10, None, None)
    assert exe.value.code == "wrong_type"
    with pytest.raises(DocumentError) as audio:
        await svc.start_upload(tenant_a, "u", "call.mp3", 10, None, None)
    assert audio.value.code == "audio_not_supported" and "transcript" in str(audio.value)
    # more bytes than declared never reach quarantine
    started = await svc.start_upload(tenant_a, "u", "a.txt", 5, None, None)
    token = started["upload_url"].split("token=")[1]

    async def body() -> object:
        yield b"0123456789"

    with pytest.raises(DocumentError) as more:
        await svc.receive(tenant_a, UUID(started["document_id"]), token, body())  # type: ignore[arg-type]
    assert more.value.code == "size_mismatch"
    with pytest.raises(DocumentError) as bad_token:
        await svc.receive(tenant_a, UUID(started["document_id"]), "x" * 30, body())  # type: ignore[arg-type]
    assert bad_token.value.code == "upload_closed"


async def test_an_unavailable_scanner_rejects_without_parsing(
    tenant_a: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = _never_parse(monkeypatch)
    svc = service(tmp_path, scanner=UnavailableScanner())
    done = await upload(svc, tenant_a, "notes.txt", b"plain notes\n")
    doc = UUID(done["document_id"])
    assert await svc.process(tenant_a, doc) == "rejected"
    assert doc_row(doc)["reject_reason"] == "scan_unavailable" and called == []
    # kept in quarantine for a later retry, never promoted
    assert (tmp_path / f"quarantine/tenants/{tenant_a}/{doc}/raw").exists()


async def test_company_is_detected_and_ambiguity_is_never_guessed(
    tenant_a: UUID, tmp_path: Path
) -> None:
    svc = service(tmp_path)
    clear = await upload(svc, tenant_a, "notes.docx", fixtures.notes_docx())
    two = await upload(svc, tenant_a, "compare.txt", fixtures.two_companies_txt())
    for d in (clear, two):
        assert (
            await svc.process(tenant_a, UUID(d["document_id"])) == "parsed"
        )  # unbound: not indexed
    detected = doc_row(clear["document_id"])["detected_company"]
    assert detected["status"] == "resolved"
    assert detected["company"]["domain"] == "northwind.example"
    assert doc_row(two["document_id"])["detected_company"]["status"] == "ambiguous"
    view = await svc.status(tenant_a, UUID(clear["document_id"]))
    assert view["needs_company"] is True
    # the user confirms: bound, then indexed by the queued task
    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    await svc.confirm_company(tenant_a, [UUID(clear["document_id"])], account)
    assert (tenant_a, UUID(clear["document_id"])) in svc.scheduler.documents
    assert await svc.process(tenant_a, UUID(clear["document_id"])) == "indexed"
    assert (
        admin(
            "SELECT count(*) FROM document_passages WHERE document_id = %s AND account_id = %s",
            clear["document_id"],
            account,
        )[0][0]
        >= 1
    )


async def test_delete_removes_chunks_and_marks_facts_for_reevaluation(
    tenant_a: UUID, tmp_path: Path
) -> None:
    from account_intelligence.grading.ports import RunInfo
    from account_intelligence.grading.repo import record_document_fact

    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, "notes.docx", fixtures.notes_docx(), account)
    doc = UUID(done["document_id"])
    await svc.process(tenant_a, doc)
    (run,) = admin(
        "INSERT INTO account_runs (tenant_id, account_id, graph_version, budget_json,"
        " reserved_credits, idempotency_key) VALUES (%s, %s, 'test', '{}', 1, %s) RETURNING id",
        tenant_a,
        account,
        f"k-{uuid4().hex[:10]}",
    )
    info = RunInfo(tenant_a, account, run[0], datetime.now(UTC), "northwind.example")
    async with tenant_txn(tenant_a) as s:
        fact, created = await record_document_fact(
            s,
            run=info,
            attribute_key="file.platform_vendor_account_team.platforms.crm",
            value="Salesforce",
            quote="uses Salesforce",
            document_id=doc,
            location={"page": 1, "passage": 1, "cite": "page 1"},
            authoritative=False,
            flags=(),
            config_hash="c" * 64,
        )
    assert created
    assert admin("SELECT visibility, product_grade FROM facts WHERE id = %s", fact) == [
        ("private", "internal")
    ]
    out = await svc.delete(tenant_a, doc, "user-1")
    assert out["chunks_removed"] >= 1 and out["facts_marked"] == 1
    for table, col in (
        ("chunks", "doc_id"),
        ("document_passages", "document_id"),
        ("document_redactions", "document_id"),
        ("documents", "id"),
    ):
        assert admin(f"SELECT count(*) FROM {table} WHERE {col} = %s", doc) == [(0,)], table  # noqa: S608
    assert admin("SELECT status FROM facts WHERE id = %s", fact) == [("needs_reevaluation",)]
    assert admin("SELECT reason FROM fact_reevaluations WHERE fact_id = %s", fact) == [
        ("source_document_deleted",)
    ]
    assert (
        admin("SELECT chunks, facts_marked FROM document_deletions WHERE document_id = %s", doc)[0][
            1
        ]
        == 1
    )
    assert not (tmp_path / f"tenants/{tenant_a}/documents/{doc}/raw").exists()


async def test_documents_orphaned_sweeper_requeues_a_dead_worker(
    tenant_a: UUID, tmp_path: Path
) -> None:
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, "notes.txt", b"notes about Northwind\n")
    doc = UUID(done["document_id"])
    admin(
        "UPDATE documents SET status = 'parsing', object_key = 'k', sha256 = %s,"
        " lease_owner = 'dead', lease_expires_at = now() - interval '1 hour' WHERE id = %s",
        "a" * 64,
        doc,
    )
    swept = await sweep_documents(
        SessionLocal, svc.scheduler, datetime.now(UTC) + timedelta(seconds=1), orphan_after_s=60
    )
    assert doc in swept and svc.scheduler.documents[-1] == (tenant_a, doc)


async def test_documents_are_invisible_to_another_tenant(
    tenant_a: UUID, tenant_b: UUID, tmp_path: Path
) -> None:
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, "notes.docx", fixtures.notes_docx())
    doc = UUID(done["document_id"])
    await svc.process(tenant_a, doc)
    with pytest.raises(DocumentError):
        await svc.status(tenant_b, doc)
    with pytest.raises(DocumentError):
        await svc.delete(tenant_b, doc, "intruder")
    assert await svc.process(tenant_b, doc) == "noop"
    assert await svc.list(tenant_b, None) == []
    async with tenant_txn(tenant_b) as s:
        assert await repo.passages(s, doc) == []
    assert doc_row(doc)["status"] == "parsed"


async def test_page_limit_stops_the_parse_with_its_reason(tenant_a: UUID, tmp_path: Path) -> None:
    from tests.research_files.fixtures import pdf_bytes

    svc = service(tmp_path)
    svc.cfg = svc.c.model_copy(
        update={"parsing": svc.c.parsing.model_copy(update={"max_pages": 2})}
    )
    done = await upload(svc, tenant_a, "long.pdf", pdf_bytes(["one", "two", "three"]))
    doc = UUID(done["document_id"])
    assert await svc.process(tenant_a, doc) == "failed"
    row = doc_row(doc)
    # the limit is per part (Phase 4B): the part fails with its reason, and says so
    assert row["stop_detail"] == "parts_failed"
    assert admin(
        "SELECT part_no, status, failure FROM document_parts WHERE document_id = %s", doc
    ) == [(0, "failed", "parse:page_limit")]
    view = await svc.status(tenant_a, doc)
    assert view["parts_failed"] == [
        {"part": 1, "reason": "This part has more pages than one part may."}
    ]
    assert admin("SELECT count(*) FROM document_passages WHERE document_id = %s", doc) == [(0,)]
