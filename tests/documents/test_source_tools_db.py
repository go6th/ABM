"""The prose access tools on Postgres (documents/sources.py, 2026-10-17): list_sources shows
every member of the caller's files with its shape and counts; search_passages is the hybrid
search on demand; read_passage and read_span hand back the exact stored text of what was cited;
everything outside the caller's accounts is not found. SYNTHETIC files only."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from account_intelligence.db.session import tenant_txn
from documents import sources as S
from documents.tables import QueryError
from tests.conftest import _make_account
from tests.documents import fixtures
from tests.documents.world import admin, service, upload

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def test_the_prose_tools_search_and_read_cited_text(tenant_a: UUID, tmp_path: Path) -> None:
    account = _make_account(tenant_a, f"acme-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    notes = await upload(svc, tenant_a, "notes.docx", fixtures.notes_docx(), account)
    assert await svc.process(tenant_a, UUID(notes["document_id"])) == "indexed"
    jobs = await upload(svc, tenant_a, "jobs.xlsx", fixtures.gsk_job_postings_xlsx(), account)
    jobs_id = UUID(jobs["document_id"])
    assert await svc.process(tenant_a, jobs_id) == "indexed"

    async with tenant_txn(tenant_a, user="user-1") as s:
        listed = await S.list_sources(s, [account])
    by_doc = {m["document"]: m for m in listed["sources"]}
    assert by_doc["notes.docx"]["shape"] == "prose"
    assert by_doc["notes.docx"]["sha256"] and by_doc["notes.docx"]["ingested_at"]
    assert by_doc["jobs.xlsx"]["shape"] == "tabular" and by_doc["jobs.xlsx"]["rows"] >= 600

    async with tenant_txn(tenant_a, user="user-1") as s:
        hits = await S.search(s, "Contoso migration", [account], None, 5, [])
        assert hits["hits"] >= 1 and "Contoso" in hits["passages"][0]["text"]
        chunk = hits["passages"][0]["chunk_id"]
        assert hits["passages"][0]["cite"]
        exact = await S.read_passage(s, chunk, [account])
        assert exact["text"].startswith(hits["passages"][0]["text"][:200])
        assert exact["document"] == "notes.docx" and exact["cite"]
        # a document filter narrows the search to the named file; an unknown field fails closed
        narrowed = await S.search(
            s,
            "migration",
            [account],
            None,
            5,
            [{"field": "document", "op": "icontains", "value": "notes"}],
        )
        assert narrowed["passages"] and all(
            p["document"] == "notes.docx" for p in narrowed["passages"]
        )
        with pytest.raises(QueryError, match="only"):
            bad = [{"field": "cells", "op": "eq", "value": "y"}]
            await S.search(s, "x", [account], None, 5, bad)
        # a span reads the exact text between two cited chunks; across files it refuses
        (first, second) = [
            r[0]
            for r in admin(
                "SELECT seq FROM document_passages WHERE document_id = %s ORDER BY seq LIMIT 2",
                jobs_id,
            )
        ]
        span = await S.read_span(s, f"{jobs_id}:{first}", f"{jobs_id}:{second}", [account])
        assert span["passages"] == 2 and not span["truncated"] and span["text"]
        with pytest.raises(QueryError, match="one file"):
            await S.read_span(s, chunk, f"{jobs_id}:1", [account])
        with pytest.raises(QueryError, match="chunk id"):
            await S.read_passage(s, "bogus", [account])
        # another account's passages are not found, never leaked
        other = _make_account(tenant_a, f"other-{uuid4().hex[:6]}.example")
        with pytest.raises(QueryError, match="no passage"):
            await S.read_passage(s, chunk, [other])
        assert (await S.list_sources(s, [other]))["count"] == 0

    # B4/B5: every stored passage carries the prose record and the document's ingest stamp
    metas = admin(
        "SELECT kind, metadata FROM document_passages WHERE document_id = %s ORDER BY seq",
        notes["document_id"],
    )
    assert metas and all(m[1] and m[1].get("ingested_at") for m in metas)
    sections = [m[1] for m in metas if m[0] == "section"]
    assert sections and all(
        m.get("kind") == "section" and m.get("heading_path", "").startswith("notes.docx")
        for m in sections
    )
    row_meta = admin(
        "SELECT metadata FROM document_passages WHERE document_id = %s AND kind = 'crm_row'"
        " LIMIT 1",
        jobs_id,
    )
    assert row_meta and row_meta[0][0].get("ingested_at")  # row passages keep cells + stamp
