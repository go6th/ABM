"""Ask reads the file a question is about whole (owner decision 2026-10-17): the best-matching
uploaded document's passages travel to the model in order as one citable row, within a character
budget; and closed postings join the rows only when the question asks about them."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import answers as A
from account_intelligence.replica.model_answer import context_block, file_blocks, pick_rows
from tests.conftest import ADMIN_DSN

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

LINES = [
    "Req | Title | Platforms named | Status | First seen",
    "446999 | Omnichannel & Digital Manager | Salesforce | Closed | 2026-09-14",
    "439962 | Sr. Product Manager, CRM Portals | Salesforce, Veeva | Closed | 2026-04-30",
]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _upload(tenant: UUID, user: str, name: str) -> UUID:
    """A parsed and indexed spreadsheet of three passages, as the documents pipeline leaves it."""
    doc = uuid4()
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        account = c.execute(
            "SELECT id FROM accounts WHERE tenant_id = %s AND domain = 'highmark.example'",
            (tenant,),
        ).fetchone()[0]
        c.execute(
            "INSERT INTO documents (id, tenant_id, account_id, name, declared_bytes, declared_type,"
            " quarantine_key, object_key, sha256, status, passages, uploaded_by,"
            " upload_expires_at, owner_user_id) VALUES (%s, %s, %s, %s, 1000, 'application/x',"
            " 'q/x', 'o/x', %s, 'indexed', 3, %s, now(), %s)",
            (doc, tenant, account, name, _sha(name), user, user),
        )
        for seq, line in enumerate(LINES, start=1):
            c.execute(
                "INSERT INTO document_passages (tenant_id, account_id, document_id, seq, kind,"
                " location, content, content_sha256, token_count, owner_user_id)"
                " VALUES (%s, %s, %s, %s, 'crm_row', %s, %s, %s, 12, %s)",
                (tenant, account, doc, seq, json.dumps({"row": seq}), line, _sha(line), user),
            )
    return doc


async def test_the_file_a_question_is_about_reaches_the_model_whole(world: dict[str, Any]) -> None:
    doc = _upload(world["a"], "priya-test", "GSK_Job_Postings.xlsx")
    # keyword retrieval needs every word of the question in a passage
    question = "Omnichannel Digital Manager Salesforce Closed"
    async with tenant_txn(world["a"]) as session:
        ws = await A.load_workspace(session, "priya-test")
        scope = {a.id for a in ws.accounts}
        ws.passages = await A.passage_rows(session, ws, question, scope, None)
        assert any(r.key.startswith(f"passage:{doc}:") for r in ws.passages)
        await A.take_whole_file(session, ws, 300_000)
    assert len(ws.whole_files) == 1
    whole = ws.whole_files[0]
    assert whole.key == f"whole:{doc}" and whole.kind == "file" and whole.grade == "internal"
    assert whole.text.split("\n") == LINES  # every passage, in order, nothing sampled
    assert whole.extra == {"whole_file": True, "document": "GSK_Job_Postings.xlsx", "lines": 3}
    assert whole.title == "From your files: GSK_Job_Postings.xlsx (the whole file)"
    # its passages leave the retrieved list: the file is cited once, as one source
    assert not any(r.key.startswith(f"passage:{doc}:") for r in ws.passages)
    # the model sees the row announcing the file and the file itself in its own block
    q, local = A.understand(question, ws, None), A.Draft()
    rows = pick_rows(q, ws, local)
    block, ids = context_block(rows)
    assert f"GSK_Job_Postings.xlsx, given whole below (3 lines) | source: {whole.title}" in block
    rid = next(k for k, r in ids.items() if r.key == whole.key)
    files = file_blocks(ids)
    assert files.startswith(f'<file id="{rid}" name="GSK_Job_Postings.xlsx">')
    assert LINES[1] in files and LINES[2] in files


async def test_a_file_over_the_budget_stays_as_retrieved_passages(world: dict[str, Any]) -> None:
    doc = _upload(world["a"], "priya-test", "GSK_Big_Export.xlsx")
    question = "Omnichannel Digital Manager Salesforce Closed"
    async with tenant_txn(world["a"]) as session:
        ws = await A.load_workspace(session, "priya-test")
        ws.passages = await A.passage_rows(session, ws, question, {a.id for a in ws.accounts}, None)
        before = list(ws.passages)
        assert any(r.key.startswith(f"passage:{doc}:") for r in before)
        await A.take_whole_file(session, ws, 40)  # smaller than the file
        assert ws.whole_files == [] and ws.passages == before
        ws.passages = []
        await A.take_whole_file(session, ws, 300_000)  # nothing retrieved: nothing to read whole
        assert ws.whole_files == []


async def test_closed_postings_are_read_only_when_asked(world: dict[str, Any]) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        closed_id = c.execute(
            "UPDATE job_postings SET status = 'closed' WHERE id = (SELECT j.id FROM job_postings j"
            " JOIN accounts a ON a.id = j.account_id WHERE j.tenant_id = %s AND"
            " a.domain = 'highmark.example' AND j.status <> 'closed' ORDER BY j.created_at LIMIT 1)"
            " RETURNING id, title",
            (world["a"],),
        ).fetchone()
    assert closed_id is not None
    key, title = f"job:{closed_id[0]}", closed_id[1]
    async with tenant_txn(world["a"]) as session:
        plain = await A.load_workspace(session, "priya-test")
        asked = await A.load_workspace(session, "priya-test", include_closed=True)
    assert key not in {r.key for r in plain.jobs}
    assert all(r.extra["status"] != "closed" for r in plain.jobs)
    row = next(r for r in asked.jobs if r.key == key)
    assert row.text == f"{title} (closed)" and row.meta.endswith(" · closed")
    assert row.extra["status"] == "closed"
    assert len(asked.jobs) == len(plain.jobs) + 1
