"""PR5 (ingestion hardening): the company is matched first, the account's rows are imported
before the embedding, and an upload that cannot be matched waits as ``needs_account``.
SYNTHETIC files only (the Holdings workspace zip, the Northwind notes)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from account_intelligence.db.session import SessionLocal, acting_as
from documents.service import DocumentError, DocumentService, sweep_documents
from tests.conftest import _make_account
from tests.documents import fixtures, workspace_zip
from tests.documents.test_deliverables_import import USER, _counts
from tests.documents.test_workspace_zip_import_db import _zip_with_twins
from tests.documents.world import admin, doc_row, service, upload

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _two_step_service(root: Path, *, match_account: bool = True) -> DocumentService:
    svc = service(root, match_account=match_account)
    svc.index_in_own_task = True  # production: the import is a step, the embedding the next task
    return svc


def _chunks(doc: UUID) -> int:
    return int(admin("SELECT count(*) FROM chunks WHERE doc_id = %s", doc)[0][0])


UPLOADER = "user-1"  # the owner tests.documents.world.upload gives a document


def _account(tenant: UUID, domain: str) -> UUID:
    return _make_account(tenant, domain)  # owner "test-owner" (USER)


def _account_of_uploader(tenant: UUID, domain: str) -> UUID:
    rows = admin(
        "INSERT INTO accounts (tenant_id, owner_user_id, domain) VALUES (%s, %s, %s) RETURNING id",
        tenant,
        UPLOADER,
        domain,
    )
    return UUID(str(rows[0][0]))


async def test_the_account_page_fills_before_any_embedding_is_written(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """Definition of done for PR5: People, Jobs, Tech stack, Signals and Sources are non-zero
    while the document is not yet indexed and has no chunk."""
    svc = _two_step_service(tmp_path, match_account=False)
    account = _account(tenant_a, workspace_zip.ACCOUNT_DOMAIN)
    with acting_as(USER):
        done = await upload(svc, tenant_a, workspace_zip.ZIP_NAME, _zip_with_twins(), account)
        doc = UUID(done["document_id"])
        # task 1: scan, read, match the company, import; the embedding is queued, not run
        status = await svc.process(tenant_a, doc)
        assert status == "indexing", doc_row(doc)["detected_company"]
        assert (tenant_a, doc) in svc.scheduler.documents
        row = doc_row(doc)
        assert row["status"] == "indexing" and row["account_id"] == account
        assert _chunks(doc) == 0
        assert admin("SELECT imported_at IS NOT NULL FROM documents WHERE id = %s", doc) == [
            (True,)
        ]
        counts = await _counts(tenant_a, account)
        for key in ("people", "jobs", "tech", "signals", "sources"):
            assert counts[key] > 0, (key, counts)
        # task 2: the embedding; the import is not run again
        before = dict(counts)
        assert await svc.process(tenant_a, doc) == "indexed"
        assert _chunks(doc) > 0
        assert await _counts(tenant_a, account) == before


async def test_an_upload_whose_company_is_another_domain_waits_for_the_user(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """Uploaded to an account but the file names another company: held, nothing imported or
    indexed; confirming the same account releases it."""
    svc = _two_step_service(tmp_path)
    account = _account(tenant_a, f"other-{uuid4().hex[:6]}.example")
    with acting_as(USER):
        done = await upload(svc, tenant_a, workspace_zip.ZIP_NAME, _zip_with_twins(), account)
        doc = UUID(done["document_id"])
        assert await svc.process(tenant_a, doc) == "needs_account"
        row = doc_row(doc)
        assert row["status"] == "needs_account" and row["account_id"] == account
        # the tables of this synthetic package carry no company evidence: nothing to match
        assert row["detected_company"]["status"] == "none"
        counts = await _counts(tenant_a, account)
        assert (counts["people"], counts["jobs"], counts["tech"]) == (0, 0, 0)
        assert _chunks(doc) == 0
        view = await svc.status(tenant_a, doc)
        assert view["stage"] == "needs_account" and view["needs_company"] is True
        assert view["message"] == "Pick the account this file belongs to."
        assert view["company"]["status"] == "none"
        # a held upload is not processed again by a duplicate task
        assert await svc.process(tenant_a, doc) == "needs_account"

        await svc.confirm_company(tenant_a, [doc], account)
        assert (tenant_a, doc) in svc.scheduler.documents
        assert await svc.process(tenant_a, doc) == "indexing"  # imported, embedding queued
        assert (await _counts(tenant_a, account))["people"] > 0
        assert await svc.process(tenant_a, doc) == "indexed"


async def test_a_held_upload_can_be_moved_to_another_account(
    tenant_a: UUID, tmp_path: Path
) -> None:
    svc = _two_step_service(tmp_path)
    wrong = _account(tenant_a, f"wrong-{uuid4().hex[:6]}.example")
    right = _account(tenant_a, f"right-{uuid4().hex[:6]}.example")
    with acting_as(USER):
        done = await upload(svc, tenant_a, "notes.docx", fixtures.notes_docx(), wrong)
        doc = UUID(done["document_id"])
        assert await svc.process(tenant_a, doc) == "needs_account"
        await svc.confirm_company(tenant_a, [doc], right)
        assert doc_row(doc)["account_id"] == right
        assert (
            admin(
                "SELECT count(*) FROM document_passages WHERE document_id = %s AND account_id = %s",
                doc,
                right,
            )[0][0]
            >= 1
        )
        assert await svc.process(tenant_a, doc) == "indexing"
        assert await svc.process(tenant_a, doc) == "indexed"
        # an indexed document is bound for good
        with pytest.raises(DocumentError):
            await svc.confirm_company(tenant_a, [doc], wrong)


async def test_an_upload_without_an_account_binds_to_the_account_of_its_company(
    tenant_a: UUID, tmp_path: Path
) -> None:
    svc = _two_step_service(tmp_path)
    account = _account_of_uploader(tenant_a, "northwind.example")
    with acting_as(UPLOADER):
        clear = await upload(svc, tenant_a, "notes.docx", fixtures.notes_docx())
        doc = UUID(clear["document_id"])
        assert await svc.process(tenant_a, doc) == "indexing"  # bound by itself, then imported
        assert doc_row(doc)["account_id"] == account
        assert await svc.process(tenant_a, doc) == "indexed"
        assert _chunks(doc) > 0


async def test_ambiguous_or_unknown_companies_wait_as_needs_account(
    tenant_a: UUID, tmp_path: Path
) -> None:
    svc = _two_step_service(tmp_path)
    _account_of_uploader(tenant_a, "northwind.example")
    with acting_as(UPLOADER):
        two = await upload(svc, tenant_a, "compare.txt", fixtures.two_companies_txt())
        assert await svc.process(tenant_a, UUID(two["document_id"])) == "needs_account"
        row = doc_row(two["document_id"])
        assert row["account_id"] is None and row["detected_company"]["status"] == "ambiguous"
        # resolved, but no account of the owner has that domain: also waits
        other = _two_step_service(tmp_path / "b")
        admin("UPDATE accounts SET domain = 'elsewhere.example' WHERE tenant_id = %s", tenant_a)
        lone = await upload(other, tenant_a, "notes.docx", fixtures.notes_docx())
        assert await other.process(tenant_a, UUID(lone["document_id"])) == "needs_account"
        assert doc_row(lone["document_id"])["account_id"] is None
        view = await other.status(tenant_a, UUID(lone["document_id"]))
        assert view["stage"] == "needs_account" and view["needs_company"] is True


async def test_a_row_waiting_at_parsed_with_no_account_reads_as_needs_account(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """Rows written before 0110 (parsed, account NULL) show and release as held uploads."""
    svc = _two_step_service(tmp_path)
    account = _account(tenant_a, f"x-{uuid4().hex[:6]}.example")
    with acting_as(USER):
        done = await upload(svc, tenant_a, "notes.docx", fixtures.notes_docx())
        doc = UUID(done["document_id"])
        await svc.process(tenant_a, doc)
        admin("UPDATE documents SET status = 'parsed' WHERE id = %s", doc)
        view = await svc.status(tenant_a, doc)
        assert view["stage"] == "needs_account" and view["needs_company"] is True
        await svc.confirm_company(tenant_a, [doc], account)
        assert (tenant_a, doc) in svc.scheduler.documents
        assert await svc.process(tenant_a, doc) == "indexing"


async def test_the_sweep_recovers_a_document_whose_import_step_died(
    tenant_a: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _two_step_service(tmp_path, match_account=False)
    account = _account(tenant_a, workspace_zip.ACCOUNT_DOMAIN)

    class Died(Exception):
        pass

    async def import_dies(tenant_id: UUID, row: Any) -> None:
        raise Died

    with acting_as(USER):
        done = await upload(svc, tenant_a, workspace_zip.ZIP_NAME, _zip_with_twins(), account)
        doc = UUID(done["document_id"])
        monkeypatch.setattr(svc, "_import_deliverables", import_dies)
        with pytest.raises(Died):
            await svc.process(tenant_a, doc)
        monkeypatch.undo()
        # the worker is gone: its lease is still held and has expired
        admin(
            "UPDATE documents SET lease_owner = 'dead', lease_expires_at = now() - interval"
            " '1 hour' WHERE id = %s",
            doc,
        )
        row = doc_row(doc)
        assert row["status"] == "indexing"
        assert admin("SELECT imported_at FROM documents WHERE id = %s", doc) == [(None,)]
        swept = await sweep_documents(
            SessionLocal, svc.scheduler, datetime.now(UTC) + timedelta(seconds=1), orphan_after_s=60
        )
        assert doc in swept and svc.scheduler.documents[-1] == (tenant_a, doc)
        assert await svc.process(tenant_a, doc) == "indexing"  # the import, then the queue
        assert (await _counts(tenant_a, account))["people"] > 0
        assert await svc.process(tenant_a, doc) == "indexed"


async def test_ask_says_when_a_bound_file_is_still_being_indexed(
    tenant_a: UUID, tmp_path: Path
) -> None:
    from account_intelligence.api.routes.ask import INDEXING_NOTICE, _indexing_notices

    svc = service(tmp_path)  # one call indexes
    account = _account(tenant_a, "northwind.example")
    other = _account(tenant_a, f"quiet-{uuid4().hex[:6]}.example")
    with acting_as(USER):
        done = await upload(svc, tenant_a, "notes.docx", fixtures.notes_docx(), account)
        doc = UUID(done["document_id"])
        # uploaded and not yet read: bound to the account, not searchable
        assert await _indexing_notices(tenant_a, account) == [INDEXING_NOTICE]
        assert await _indexing_notices(tenant_a, None) == [INDEXING_NOTICE]
        assert await _indexing_notices(tenant_a, other) == []
        assert await svc.process(tenant_a, doc) == "indexed"
        assert await _indexing_notices(tenant_a, account) == []
        assert await _indexing_notices(tenant_a, None) == []


async def test_the_default_rule_holds_a_file_about_another_company_and_trusts_one_naming_none(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """The production rule (config/research_input_modes.yaml): an upload sent to an account is
    held only when the file names another company; a workspace zip that names none proceeds on
    the account the user chose, so its account page fills (PR5 review, 2026-11-04)."""
    from documents.config import load_config
    from tests.documents import fixtures

    cfg = load_config().company
    assert cfg.hold_mismatched_for_account and not cfg.hold_unmatched_for_account
    svc = _two_step_service(tmp_path)
    svc.cfg = svc.c.model_copy(update={"company": cfg})  # the production rule, unchanged
    account = _account(tenant_a, f"other-{uuid4().hex[:6]}.example")
    with acting_as(USER):
        named = await upload(svc, tenant_a, "notes.docx", fixtures.notes_docx(), account)
        named_doc = UUID(named["document_id"])
        assert await svc.process(tenant_a, named_doc) == "needs_account"
        assert doc_row(named_doc)["detected_company"]["status"] == "resolved"
        package = await upload(svc, tenant_a, workspace_zip.ZIP_NAME, _zip_with_twins(), account)
        package_doc = UUID(package["document_id"])
        assert await svc.process(tenant_a, package_doc) != "needs_account"
        assert doc_row(package_doc)["status"] != "needs_account"


async def test_an_import_that_always_fails_runs_once_and_the_file_is_still_indexed(
    tenant_a: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR5 review B1 (2026-11-04): the import step runs once per document. An import that fails
    every time is recorded, the document is embedded on the next task anyway (Ask can search it),
    and the sweep's backfill tries the import again later, bounded (not on every task)."""
    from documents import deliverables

    calls: list[str] = []

    async def boom(txn: Any, tenant_id: UUID, doc: Any, **kwargs: Any) -> Any:
        calls.append(str(doc.id))
        raise RuntimeError("synthetic import failure")

    monkeypatch.setattr(deliverables, "import_document", boom)
    svc = _two_step_service(tmp_path, match_account=False)
    account = _account(tenant_a, f"holdings-{uuid4().hex[:6]}.example")
    with acting_as(USER):
        done = await upload(svc, tenant_a, workspace_zip.ZIP_NAME, _zip_with_twins(), account)
        doc = UUID(done["document_id"])
        assert await svc.process(tenant_a, doc) == "indexing"  # the import step, then the index
        assert await svc.process(tenant_a, doc) == "indexed"
        assert await svc.process(tenant_a, doc) == "indexed"
    assert calls.count(str(doc)) == 1
    assert admin("SELECT status FROM import_runs WHERE document_id = %s", doc) == [("failed",)]
    assert _chunks(doc) > 0
    await deliverables.backfill_imports(
        datetime.now(UTC) + timedelta(minutes=6), payloads=svc.payloads, blobs=svc.blobs
    )
    assert calls.count(str(doc)) == 2
