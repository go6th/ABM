"""Document retrieval for Ask (documents.retrieve, audit 2026-10-15): an indexed document's
passages are found by keyword and by meaning, only within the asked accounts, and chunks indexed
before the retrieval vectors existed get theirs when they are first searched. SYNTHETIC files."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from account_intelligence.db.session import tenant_txn
from documents.embed import FastEmbedder
from documents.retrieve import retrieve
from tests.conftest import _make_account
from tests.documents import fixtures
from tests.documents.world import admin, service, upload

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

EMBEDDER = FastEmbedder()


async def _indexed(tenant: UUID, root: Path, *, semantic: bool) -> tuple[UUID, UUID]:
    account = _make_account(tenant, f"northwind-{uuid4().hex[:6]}.example")
    svc = service(root)
    svc.semantic = EMBEDDER if semantic else None
    done = await upload(svc, tenant, "notes.docx", fixtures.notes_docx(), account)
    doc = UUID(done["document_id"])
    assert await svc.process(tenant, doc) == "indexed"
    return account, doc


async def test_passages_are_found_by_keyword_and_by_meaning(tenant_a: UUID, tmp_path: Path) -> None:
    account, doc = await _indexed(tenant_a, tmp_path, semantic=True)
    stored = admin(
        "SELECT count(*), bool_and(semantic_model = 'bge-small-en-v1.5') FROM chunks"
        " WHERE doc_id = %s",
        doc,
    )
    assert stored[0][0] >= 1 and stored[0][1] is True  # indexed with retrieval vectors
    async with tenant_txn(tenant_a) as s:
        # keyword: the exact word appears in the passage
        by_word = await retrieve(s, "Contoso migration", [account], EMBEDDER)
        # meaning: no shared keyword with "Chief Information Officer"
        by_meaning = await retrieve(s, "who runs IT at the company?", [account], EMBEDDER)
        elsewhere = await retrieve(s, "Contoso migration", [uuid4()], EMBEDDER)
    assert by_word and "Contoso" in by_word[0].content
    assert any("Chief Information Officer" in p.content for p in by_meaning)
    assert all(p.account_id == account for p in by_word + by_meaning)
    assert by_word[0].document == "notes.docx"
    assert elsewhere == []  # only the asked accounts are searched


async def test_chunks_from_before_get_their_vectors_when_first_searched(
    tenant_a: UUID, tmp_path: Path
) -> None:
    account, doc = await _indexed(tenant_a, tmp_path, semantic=False)
    assert (
        admin("SELECT count(*) FROM chunks WHERE doc_id = %s AND semantic_model IS NULL", doc)[0][0]
        >= 1
    )
    async with tenant_txn(tenant_a) as s:
        found = await retrieve(s, "who runs IT at the company?", [account], EMBEDDER)
    assert any("Chief Information Officer" in p.content for p in found)
    assert (
        admin("SELECT count(*) FROM chunks WHERE doc_id = %s AND semantic_model IS NULL", doc)[0][0]
        == 0
    )


async def test_what_was_read_lists_a_documents_passages_with_their_place(
    tenant_a: UUID, tmp_path: Path
) -> None:
    from fastapi import HTTPException

    from account_intelligence.api.errors import APIProblem
    from account_intelligence.api.routes.documents import passages

    account, doc = await _indexed(tenant_a, tmp_path, semantic=False)

    class Ident:
        tenant_id = tenant_a

    page = await passages(doc, None, -1, 50, Ident())  # type: ignore[arg-type]
    assert page["document_id"] == str(doc) and page["passages"]
    assert any("Chief Information Officer" in p["content"] for p in page["passages"])
    assert all(p["kind"] == "section" for p in page["passages"])
    first = await passages(doc, None, -1, 1, Ident())  # type: ignore[arg-type]
    assert len(first["passages"]) == 1 and first["next_after"] == first["passages"][0]["seq"]
    try:
        await passages(uuid4(), None, -1, 50, Ident())  # type: ignore[arg-type]
        raise AssertionError("an unknown document must be 404")
    except (APIProblem, HTTPException) as refused:
        assert getattr(refused, "status", getattr(refused, "status_code", None)) == 404


async def test_two_workers_indexing_one_document_never_deadlock(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """Two workers index the same page of one document at once (seen on staging 2026-10-15 as a
    deadlock): the document row is locked for update first, so the second waits, both succeed
    and the page is stored once."""
    import asyncio

    from documents import repo

    account, doc = await _indexed(tenant_a, tmp_path, semantic=False)
    async with tenant_txn(tenant_a) as s:
        rows = await repo.passages_after(s, doc, -1, 100)
    vectors = [[0.0] * 1535 + [1.0] for _ in rows]

    async def index() -> bool:
        async with tenant_txn(tenant_a) as s:
            done = await repo.index_page(s, doc, account, rows, vectors, "local-hash-v1")
            await asyncio.sleep(0.2)  # hold the transaction so the two overlap
            return done

    assert await asyncio.gather(index(), index()) == [True, True]
    assert admin("SELECT count(*) FROM chunks WHERE doc_id = %s", doc) == [(len(rows),)]


async def test_a_zip_is_read_member_by_member_with_each_file_cited(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """A zip of documents is one upload: scanned, then every member is read and each passage
    names the file inside the zip it came from (owner request 2026-10-15)."""
    import io
    import zipfile

    from documents.chunking import cite

    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("meetings/notes.docx", fixtures.notes_docx())
        z.writestr("crm/contacts.xlsx", fixtures.crm_xlsx())
        z.writestr("model.bin", bytes(range(256)) * 2)  # listed, not a document type
    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, "account-pack.zip", raw.getvalue(), account)
    doc = UUID(done["document_id"])
    assert await svc.process(tenant_a, doc) == "indexed"
    locations = [
        r[0] for r in admin("SELECT location FROM document_passages WHERE document_id = %s", doc)
    ]
    members = {(loc.get("from", loc)).get("member") for loc in locations}
    assert members == {"meetings/notes.docx", "crm/contacts.xlsx"}
    cites = {cite(loc) for loc in locations}
    assert any(c.startswith("meetings/notes.docx") for c in cites)
    assert any(c.startswith("crm/contacts.xlsx, row") for c in cites)
    # the file list says how many members were read and names the one that was not
    view = await svc.status(tenant_a, doc)
    assert view["members_read"] == 2
    assert view["members_skipped"] == [{"member": "model.bin", "reason": "not a document type"}]
