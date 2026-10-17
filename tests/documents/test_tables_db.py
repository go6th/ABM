"""The table store on Postgres (tabular questions, 2026-10-17): an uploaded workbook's rows are
kept whole and typed, described and queried as complete sets; two ingests of one file are
identical; unknown columns are a structured error; a table past the passage cap fails its part.
SYNTHETIC workbook of the GSK Job Postings shape (``tests.documents.fixtures``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from account_intelligence.db.session import tenant_txn
from documents import tables as T
from tests.conftest import _make_account
from tests.documents import fixtures
from tests.documents.world import admin, doc_row, service, upload

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

QUESTION_WHERE = [
    {"column": "platforms_named", "op": "icontains", "value": "salesforce"},
    {"column": "status", "op": "eq", "value": "Open"},
    {"column": "first_seen", "op": "gte", "value": "2026-03-01"},
]


async def _ingest(tenant: UUID, tmp_path: Path, account: UUID, data: bytes) -> UUID:
    svc = service(tmp_path)
    done = await upload(svc, tenant, "GSK_Job_Postings.xlsx", data, account)
    doc = UUID(done["document_id"])
    assert await svc.process(tenant, doc) == "indexed"
    return doc


async def _jobs(tenant: UUID, account: UUID) -> T.TableInfo:
    async with tenant_txn(tenant, user="user-1") as s:
        found = await T.list_tables(s, [account])
    return T.find_table(found, "Jobs")


async def _query(tenant: UUID, table: T.TableInfo, **payload: Any) -> dict[str, Any]:
    async with tenant_txn(tenant, user="user-1") as s:
        return await T.run(s, T.parse(table, payload))


async def test_a_tabular_question_is_answered_from_the_table_store_not_a_sample(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """The golden tabular question over the store with a real Postgres: the strict query is
    empty (with the relax directive), the relaxed one is complete, deduplicated, sorted and
    projected, and the 5,001st match is an error, never a silent cut."""
    account = _make_account(tenant_a, f"gsk-{uuid4().hex[:6]}.example")
    doc = await _ingest(tenant_a, tmp_path, account, fixtures.gsk_job_postings_xlsx())
    assert doc_row(doc)["status"] == "indexed"
    jobs = await _jobs(tenant_a, account)
    assert jobs.row_count == 644 and jobs.business_key == "requisition"
    types = {c.name: c.type for c in jobs.columns}
    assert types["requisition"] == "number" and types["first_seen"] == "date"
    assert types["status"] == "text" and types["last_seen"] == "date"
    assert [c.name for c in jobs.columns if c.payload] == ["full_description"]
    # the row passage embeds the short columns only; the description is payload, kept whole
    (first,) = admin(
        "SELECT content, metadata FROM document_passages WHERE document_id = %s"
        " AND location->>'table' = 'Jobs' AND (location->>'row')::int = 2"
        " AND coalesce((location->>'part')::int, 1) = 1",
        doc,
    )
    assert len(first[0]) < 1500 and "Full description" not in first[0]
    assert len(first[1]["full_description"]) > 5000 and first[1]["first_seen"] == "2026-03-01"
    assert first[1]["requisition"] == 446999 and "Full description" in "; ".join(
        r[0]
        for r in admin(
            "SELECT content FROM document_passages WHERE document_id = %s AND"
            " (location->>'row')::int = 2 AND location->>'table' = 'Jobs' ORDER BY seq",
            doc,
        )
    )
    async with tenant_txn(tenant_a, user="user-1") as s:
        described = await T.describe(s, jobs)
    assert described["row_count"] == 644 and described["business_key"] == "requisition"
    status = next(c for c in described["columns"] if c["name"] == "status")
    assert status["header"] == "Status" and set(status["samples"]) <= {"Open", "Closed"}
    assert all(len(str(v)) <= 80 for c in described["columns"] for v in c["samples"])
    # the golden question: no open Salesforce posting first seen on or after 1 March 2026
    strict = await _query(tenant_a, jobs, where=QUESTION_WHERE)
    assert strict["row_count"] == 0 and strict["rows"] == [] and not strict["truncated"]
    # an empty result tells the model what to do next (the relax directive of TABULAR_RULES)
    assert "relax" in strict["next"] and "status" in strict["next"]
    assert strict["applied_where"][2] == {
        "column": "first_seen",
        "op": "gte",
        "value": "2026-03-01",
    }
    # relaxing the status (exactly one predicate) finds the 20 rows, 14 unique postings
    relaxed = await _query(tenant_a, jobs, where=QUESTION_WHERE[::2])
    assert relaxed["row_count"] == 20 and len(relaxed["rows"]) == 20
    assert all(r["table"] == "Jobs" and r["row"] >= 2 for r in relaxed["rows"])
    assert all(r["cells"]["status"] == "Closed" for r in relaxed["rows"])
    assert "full_description" not in relaxed["rows"][0]["cells"]  # payload only on request
    unique = await _query(tenant_a, jobs, where=QUESTION_WHERE[::2], distinct_on="requisition")
    assert unique["row_count"] == 14 and unique["dedup_removed"] == 6
    owner = [r for r in unique["rows"] if r["cells"]["class"] in fixtures.OWNER_CLASSES]
    assert len(owner) == 4
    named = {r["cells"]["requisition"] for r in unique["rows"]}
    assert set(fixtures.GSK_FALSE_POSITIVES) <= named
    # the survivor of a duplicate requisition is the row seen last
    twice = await _query(
        tenant_a, jobs, where=[{"column": "requisition", "op": "eq", "value": 446999}]
    )
    assert twice["row_count"] == 2
    latest = max(twice["rows"], key=lambda r: r["cells"]["last_seen"])
    kept = next(r for r in unique["rows"] if r["cells"]["requisition"] == 446999)
    assert kept["row"] == latest["row"]
    several = await _query(
        tenant_a,
        jobs,
        where=[{"column": "class", "op": "in", "value": list(fixtures.OWNER_CLASSES)}],
    )
    assert several["row_count"] == 6  # four postings, two of them in two rows


async def test_query_table_returns_complete_sets(tenant_a: UUID, tmp_path: Path) -> None:
    """Acceptances 3/4 of the data-access brief on their own: a filtered result is the whole
    set, never a top-k sample — payload on request, ordering honoured, null cells queryable."""
    account = _make_account(tenant_a, f"gsk-{uuid4().hex[:6]}.example")
    await _ingest(tenant_a, tmp_path, account, fixtures.gsk_job_postings_xlsx())
    jobs = await _jobs(tenant_a, account)
    # payload on request; ordering; a filtered result is the whole set, never a top-k
    with_text = await _query(
        tenant_a,
        jobs,
        where=[{"column": "requisition", "op": "eq", "value": 446999}],
        columns=["title", "full_description"],
        limit=1,
    )
    assert with_text["truncated"] and len(with_text["rows"][0]["cells"]["full_description"]) > 5000
    closed = await _query(
        tenant_a,
        jobs,
        where=[{"column": "status", "op": "eq", "value": "closed"}],
        order_by=[{"column": "first_seen", "desc": True}],
        limit=5000,
    )
    assert closed["row_count"] >= 300 and len(closed["rows"]) == closed["row_count"]
    seen = [r["cells"]["first_seen"] for r in closed["rows"]]
    assert seen == sorted(seen, reverse=True)
    nulls = await _query(tenant_a, jobs, where=[{"column": "requisition", "op": "is_null"}])
    assert nulls["row_count"] == 4


async def test_query_table_refuses_unknown_columns(tenant_a: UUID, tmp_path: Path) -> None:
    account = _make_account(tenant_a, f"gsk-{uuid4().hex[:6]}.example")
    await _ingest(tenant_a, tmp_path, account, fixtures.gsk_job_postings_xlsx())
    jobs = await _jobs(tenant_a, account)
    with pytest.raises(T.QueryError) as unknown:
        T.parse(jobs, {"where": [{"column": "platform", "op": "eq", "value": "x"}]})
    assert (
        unknown.value.code == "unknown_column"
        and "platforms_named" in unknown.value.detail["columns"]
    )
    assert unknown.value.payload()["error"]["code"] == "unknown_column"
    with pytest.raises(T.QueryError, match="unknown op"):
        T.parse(jobs, {"where": [{"column": "status", "op": "like", "value": "x"}]})
    with pytest.raises(T.QueryError, match="ISO date"):
        T.parse(jobs, {"where": [{"column": "first_seen", "op": "gte", "value": "March"}]})
    with pytest.raises(T.QueryError, match="limit"):
        T.parse(jobs, {"limit": 0})
    with pytest.raises(T.QueryError, match="No table named"):
        T.find_table([jobs], "People")
    # the header as written is accepted and mapped to the normalised name
    parsed = T.parse(
        jobs, {"where": [{"column": "Platforms named", "op": "icontains", "value": "sap"}]}
    )
    assert parsed.where[0].column.name == "platforms_named"
    # another tenant sees no table at all (row level security and the account scope)
    other = _make_account(tenant_a, f"other-{uuid4().hex[:6]}.example")
    async with tenant_txn(tenant_a, user="user-1") as s:
        assert await T.list_tables(s, [other]) == []


async def test_two_ingests_of_one_workbook_are_identical(tenant_a: UUID, tmp_path: Path) -> None:
    account = _make_account(tenant_a, f"gsk-{uuid4().hex[:6]}.example")
    data = fixtures.gsk_job_postings_xlsx()
    # two service instances over THE SHARED root (0078: the raw store is content-addressed, so
    # the second ingest reuses the first's object, exactly as two worker replicas share a bucket)
    one = await _ingest(tenant_a, tmp_path, account, data)
    two = await _ingest(tenant_a, tmp_path, account, data)
    shas = [
        {
            r[0]
            for r in admin("SELECT content_sha256 FROM document_passages WHERE document_id = %s", d)
        }
        for d in (one, two)
    ]
    assert shas[0] == shas[1] and len(shas[0]) > 644
    schemas = [
        admin(
            "SELECT table_name, columns, payload_columns, business_key, row_count FROM"
            " document_tables WHERE document_id = %s ORDER BY table_name",
            d,
        )
        for d in (one, two)
    ]
    assert schemas[0] == schemas[1]
    cells = [
        admin(
            "SELECT t.table_name, r.row_no, r.cells FROM document_table_rows r JOIN"
            " document_tables t ON t.id = r.table_id WHERE r.document_id = %s"
            " ORDER BY t.table_name, r.row_no",
            d,
        )
        for d in (one, two)
    ]
    assert cells[0] == cells[1]


async def test_a_table_past_the_passage_cap_fails_its_part(tenant_a: UUID, tmp_path: Path) -> None:
    from documents.config import load_config

    account = _make_account(tenant_a, f"gsk-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    cfg = load_config()
    svc.cfg = cfg.model_copy(
        update={"parsing": cfg.parsing.model_copy(update={"max_passages": 50})}
    )
    done = await upload(svc, tenant_a, "crm.xlsx", fixtures.gsk_job_postings_xlsx(), account)
    doc = UUID(done["document_id"])
    assert await svc.process(tenant_a, doc) == "failed"
    status = await svc.status(tenant_a, doc)
    assert (
        status["parts_failed"]
        and "more rows than one part may hold" in status["parts_failed"][0]["reason"]
    )
    assert admin(
        "SELECT count(*) FROM document_tables WHERE document_id = %s AND row_count > 0", doc
    ) == [(0,)]


async def test_reingest_tables_queues_pre_store_documents_once(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """A document ingested before the table store (crm_row passages, no table record) is reset
    and queued once; a document already in the store is left alone; a second run queues
    nothing."""
    from account_intelligence.db.session import tenant_txn, untenanted_session
    from documents.tables_reingest import reingest_tables

    account = _make_account(tenant_a, f"gsk-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, "crm.xlsx", fixtures.crm_xlsx(), account)
    stored = UUID(done["document_id"])
    assert await svc.process(tenant_a, stored) == "indexed"
    # an older document: its table record removed, as a pre-0077 ingest left it
    done = await upload(svc, tenant_a, "old.xlsx", fixtures.crm_xlsx(), account)
    old = UUID(done["document_id"])
    assert await svc.process(tenant_a, old) == "indexed"
    admin("DELETE FROM document_tables WHERE document_id = %s", old)
    queue = svc.scheduler
    queue.documents.clear()
    first = await reingest_tables(untenanted_session, tenant_txn, queue)
    assert first["queued"] >= 1 and (tenant_a, old) in queue.documents
    assert (tenant_a, stored) not in queue.documents
    assert doc_row(old)["status"] == "parsing" and doc_row(stored)["status"] == "indexed"
    assert admin("SELECT count(*) FROM document_passages WHERE document_id = %s", old) == [(0,)]
    assert admin("SELECT status FROM document_parts WHERE document_id = %s", old) == [("pending",)]
    queue.documents.clear()
    second = await reingest_tables(untenanted_session, tenant_txn, queue)
    assert (tenant_a, old) not in queue.documents and second["queued"] <= first["queued"] - 1
    # the queued read restores the document with its tables
    assert await svc.process(tenant_a, old) == "indexed"
    assert admin("SELECT count(*) FROM document_tables WHERE document_id = %s", old) == [(1,)]


async def test_pii_never_reaches_store_or_tools(tenant_a: UUID, tmp_path: Path) -> None:
    """Acceptance 13 of the data-access brief: under the strict tier the workbook's "Lee Park"
    (a name column) is stored only as its ``[PERSON_NAME_n]`` placeholder — never in the table
    store, the passages or their metadata, the private index, the embedder's input, or anything
    the table tools return."""
    import json
    from dataclasses import dataclass, field

    from documents import repo

    @dataclass
    class SpyEmbedder:
        """The embedder with its input recorded (acceptance 13's embedding leg)."""

        inner: Any
        texts: list[str] = field(default_factory=list)

        @property
        def model(self) -> str:
            """The inner model's name."""
            return str(self.inner.model)

        @property
        def dimensions(self) -> int:
            """The inner model's dimensions."""
            return int(self.inner.dimensions)

        async def embed(self, texts: Any) -> Any:
            """Record the input, then embed as the inner model would."""
            self.texts.extend(texts)
            return await self.inner.embed(texts)

        def cost_usd(self, texts: Any) -> float:
            """The inner model's cost."""
            return float(self.inner.cost_usd(texts))

    account = _make_account(tenant_a, f"strict-{uuid4().hex[:6]}.example")
    async with tenant_txn(tenant_a, user="user-1") as s:
        await repo.set_policy(s, "strict", "user-1")
    svc = service(tmp_path)
    spy = SpyEmbedder(svc.embedder)
    svc.embedder = spy
    done = await upload(svc, tenant_a, "crm.xlsx", fixtures.crm_xlsx(), account)
    doc = UUID(done["document_id"])
    assert await svc.process(tenant_a, doc) == "indexed"
    # the redaction engaged: the placeholder is what the stores hold
    assert (
        admin(
            "SELECT count(*) FROM document_passages WHERE document_id = %s"
            " AND content LIKE '%%PERSON_NAME_%%'",
            doc,
        )[0][0]
        >= 1
    )
    pii = "%Lee Park%"
    # the raw bytes on disk are the only place the seed survives (the source of truth)
    for sql in (
        "SELECT count(*) FROM document_table_rows WHERE document_id = %s AND cells::text LIKE %s",
        "SELECT count(*) FROM document_passages WHERE document_id = %s AND (content LIKE %s OR"
        " metadata::text LIKE %s)",
        "SELECT count(*) FROM chunks WHERE doc_id = %s AND content LIKE %s",
    ):
        assert admin(sql, *([doc, pii, pii] if sql.count("%s") == 3 else [doc, pii])) == [(0,)]
    assert not any("Lee Park" in text for text in spy.texts)
    # the tools: describe, the full query, and a whole-row read return placeholders only
    async with tenant_txn(tenant_a, user="user-1") as s:
        table = T.find_table(await T.list_tables(s, [account]), "Contacts")
        described = await T.describe(s, table)
        queried = await T.run(s, T.parse(table, {"table": table.name}))
        assert queried["row_count"] >= 1
        nos, cols = T.parse_read(
            table, {"table": table.name, "rows": [int(r["row"]) for r in queried["rows"][:2]]}
        )
        read = await T.read(s, table, nos, cols)
    blob = json.dumps([described, queried, read], default=str)
    assert "Lee Park" not in blob and "PERSON_NAME_" in blob


async def test_reindex_requeues_the_workspace_indexed_documents_only(
    tenant_a: UUID, tenant_b: UUID, tmp_path: Path
) -> None:
    """``reindex --workspace`` resets and queues every indexed document of that client — with a
    table store record and without preference — and no other client's; a queued read rebuilds
    the document as the current pipeline writes it."""
    from account_intelligence.db.session import untenanted_session
    from documents.reindex import reindex_documents

    account = _make_account(tenant_a, f"gsk-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    first = UUID(
        (await upload(svc, tenant_a, "re-a.xlsx", fixtures.crm_xlsx(), account))["document_id"]
    )
    second = UUID(
        (await upload(svc, tenant_a, "re-b.xlsx", fixtures.crm_xlsx(), account))["document_id"]
    )
    assert await svc.process(tenant_a, first) == "indexed"
    assert await svc.process(tenant_a, second) == "indexed"
    other = UUID(
        (
            await upload(
                svc,
                tenant_b,
                "re-c.xlsx",
                fixtures.crm_xlsx(),
                _make_account(tenant_b, f"gsk-{uuid4().hex[:6]}.example"),
            )
        )["document_id"]
    )
    assert await svc.process(tenant_b, other) == "indexed"
    queue = svc.scheduler
    queue.documents.clear()
    counts = await reindex_documents(untenanted_session, tenant_txn, queue, workspace=tenant_a)
    assert counts == {"queued": 2, "skipped": 0, "clients": 1}
    assert sorted(d for _, d in queue.documents) == sorted([first, second])
    assert doc_row(first)["status"] == "parsing" and doc_row(second)["status"] == "parsing"
    assert doc_row(other)["status"] == "indexed"  # another workspace: never touched
    assert admin("SELECT count(*) FROM document_passages WHERE document_id = %s", first) == [(0,)]
    parts = admin("SELECT status FROM document_parts WHERE document_id = %s", first)
    assert parts == [("pending",)]
    # the queued reads rebuild the documents as the current pipeline writes them
    assert await svc.process(tenant_a, first) == "indexed"
    assert admin("SELECT count(*) FROM document_tables WHERE document_id = %s", first) == [(1,)]


def _tags_xlsx() -> bytes:
    """A small workbook whose Tags column is multi-valued (half its text cells are lists),
    with one "Salesforce Effectiveness" substring-only cell."""
    import io

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Listings"
    ws.append(["Id", "Tags", "Notes"])
    for i, (tags, note) in enumerate(
        [
            ("Salesforce, Veeva", "first note"),
            ("Workday, Salesforce", "second"),
            ("Salesforce Effectiveness", "reads like the platform"),
            ("Veeva", "no mention"),
            ("Salesforce; Workday", "fifth"),
            ("SAP", "none"),
        ]
    ):
        ws.append([100 + i, tags, note])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


async def test_contains_on_a_multi_valued_column_marks_substring_only_rows_partial(
    tenant_a: UUID, tmp_path: Path
) -> None:
    account = _make_account(tenant_a, f"tags-{uuid4().hex[:6]}.example")
    await _ingest(tenant_a, tmp_path, account, _tags_xlsx())

    async with tenant_txn(tenant_a, user="user-1") as s:
        (listing,) = await T.list_tables(s, [account])
        described = await T.describe(s, listing)
    column = next(c for c in described["columns"] if c["name"] == "tags")
    assert column["multi_valued"] is True and column["type"] == "text"
    found = {c.name: c.multi_valued for c in listing.columns}
    assert found == {"id": False, "tags": True, "notes": False}

    out = await _query(
        tenant_a,
        listing,
        where=[{"column": "tags", "op": "icontains", "value": "salesforce"}],
        order_by=[{"column": "id"}],
    )
    rows = {str(r["cells"]["tags"]): bool(r.get("partial")) for r in out["rows"]}
    assert rows == {
        "Salesforce, Veeva": False,  # a whole list item
        "Workday, Salesforce": False,
        "Salesforce; Workday": False,
        "Salesforce Effectiveness": True,  # substring only: partial
    }
    assert out["row_count"] == 4 and out["partial_rows"] == 1
    # case-folded item equality differs from the case-sensitive op
    folded = await _query(
        tenant_a, listing, where=[{"column": "tags", "op": "contains", "value": "Salesforce"}]
    )
    assert folded["row_count"] == 4 and folded["partial_rows"] == 1


async def test_read_rows_returns_whole_rows_payload_included(
    tenant_a: UUID, tmp_path: Path
) -> None:
    from account_intelligence.replica.tools import TableTools

    account = _make_account(tenant_a, f"gsk-{uuid4().hex[:6]}.example")
    await _ingest(tenant_a, tmp_path, account, fixtures.gsk_job_postings_xlsx())
    jobs = await _jobs(tenant_a, account)
    tools = TableTools(lambda: tenant_txn(tenant_a, user="user-1"), [jobs])
    # a table is described before anything is read from it
    refused = await tools.run("read_rows", {"table": "Jobs", "rows": [2]})
    assert refused["error"]["code"] == "describe_first"
    described = await tools.run("describe_table", {"table": "Jobs"})
    assert (
        next(c for c in described["columns"] if c["name"] == "platforms_named").get("multi_valued")
        is False
    )  # well under a fifth of the sheet's cells are lists

    got = await tools.run("read_rows", {"table": "Jobs", "rows": [2, 999999]})
    assert got["row_count"] == 1 and got["not_found"] == [999999]
    cells = got["rows"][0]["cells"]
    assert got["rows"][0]["row"] == 2 and len(cells["full_description"]) > 5000
    assert cells["title"] and cells["status"] == "Closed"
    # a projection reads only the named columns, and column names are whitelisted the same way
    narrow = await tools.run(
        "read_rows", {"table": "Jobs", "rows": [2], "columns": ["Title", "status"]}
    )
    assert sorted(narrow["rows"][0]["cells"]) == ["status", "title"]
    bad = await tools.run("read_rows", {"table": "Jobs", "rows": [2], "columns": ["nope"]})
    assert bad["error"]["code"] == "unknown_column"
    too_many = await tools.run(
        "read_rows", {"table": "Jobs", "rows": list(range(1, T.READ_ROWS_MAX + 2))}
    )
    assert too_many["error"]["code"] == "bad_rows"
