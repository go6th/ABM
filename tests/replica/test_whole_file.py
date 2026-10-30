"""Ask reads the file a question is about whole (owner decision 2026-10-17): the best-matching
uploaded document's passages travel to the model in order as one citable row, within a character
budget; inside an uploaded zip the file is the member the best passage came from, not the whole
archive; and closed postings join the rows only when the question asks about them."""

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
    "546999 | Omnichannel & Digital Manager | Salesforce | Closed | 2026-09-14",
    "539962 | Sr. Product Manager, CRM Portals | Salesforce, Veeva | Closed | 2026-04-30",
]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


Passage = tuple[str, dict[str, Any], str]  # kind, location, content


def _upload(tenant: UUID, user: str, name: str, passages: list[Passage] | None = None) -> UUID:
    """A parsed and indexed document, as the documents pipeline leaves it: a spreadsheet of
    three row passages unless ``passages`` says otherwise."""
    doc = uuid4()
    rows = passages or [("crm_row", {"row": i}, line) for i, line in enumerate(LINES, start=1)]
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        account = c.execute(
            "SELECT id FROM accounts WHERE tenant_id = %s AND domain = 'highmark.example'",
            (tenant,),
        ).fetchone()[0]
        c.execute(
            "INSERT INTO documents (id, tenant_id, account_id, name, declared_bytes, declared_type,"
            " quarantine_key, object_key, sha256, status, passages, uploaded_by,"
            " upload_expires_at, owner_user_id) VALUES (%s, %s, %s, %s, 1000, 'application/x',"
            " 'q/x', 'o/x', %s, 'indexed', %s, %s, now(), %s)",
            (doc, tenant, account, name, _sha(name), len(rows), user, user),
        )
        for seq, (kind, location, line) in enumerate(rows, start=1):
            c.execute(
                "INSERT INTO document_passages (tenant_id, account_id, document_id, seq, kind,"
                " location, content, content_sha256, token_count, owner_user_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 12, %s)",
                (tenant, account, doc, seq, kind, json.dumps(location), line, _sha(line), user),
            )
    return doc


async def test_the_file_a_question_is_about_reaches_the_model_whole(world: dict[str, Any]) -> None:
    doc = _upload(world["a"], "priya-test", "Holdings_Job_Postings.xlsx")
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
    assert whole.extra == {"whole_file": True, "document": "Holdings_Job_Postings.xlsx", "lines": 3}
    assert whole.title == "From your files: Holdings_Job_Postings.xlsx (the whole file)"
    # its passages leave the retrieved list: the file is cited once, as one source
    assert not any(r.key.startswith(f"passage:{doc}:") for r in ws.passages)
    # the model sees the row announcing the file and the file itself in its own block
    q, local = A.understand(question, ws, None), A.Draft()
    rows = pick_rows(q, ws, local)
    block, ids = context_block(rows)
    announced = f'Holdings_Job_Postings.xlsx, given whole in <file doc="{doc}"> (3 lines)'
    assert f"{announced} | source: {whole.title}" in block
    files = file_blocks(ids)
    assert files.startswith(f'<file doc="{doc}" name="Holdings_Job_Postings.xlsx">')
    assert LINES[1] in files and LINES[2] in files


async def test_a_file_over_the_budget_stays_as_retrieved_passages(world: dict[str, Any]) -> None:
    doc = _upload(world["a"], "priya-test", "Holdings_Big_Export.xlsx")
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


POSTINGS = [
    "Req | Title | Platforms named | Status | First seen",
    "451201 | Veeva CRM Migration Lead | Veeva | Open | 2026-10-02",
    "451377 | ServiceNow ITSM Architect | ServiceNow | Open | 2026-10-09",
]
PEOPLE = ["Name | Title | Platform owned", "Asha Rao | VP Digital Engagement | Salesforce"]


async def test_inside_a_zip_only_the_member_file_is_read_whole(world: dict[str, Any]) -> None:
    """Holdings_workspace.zip is one document whose passages span two member files; a question about
    the postings sheet gets that sheet whole, named after it, and the people sheet stays out."""
    sheet, people = "Holdings_Job_Postings.xlsx", "Holdings_People.xlsx"
    doc = _upload(
        world["a"],
        "priya-test",
        "Holdings_workspace.zip",
        [
            # rows carry the member at the top level, as chunking leaves a sheet inside a zip
            *[
                ("crm_row", {"table": f"{sheet}:Postings", "row": i, "member": sheet}, line)
                for i, line in enumerate(POSTINGS, start=1)
            ],
            # a section merged across units carries it under "from" (documents.chunking.cite)
            (
                "section",
                {"from": {"member": sheet, "section": "Notes"}, "to": {"member": sheet}},
                "Notes: open requisitions only",
            ),
            *[
                ("crm_row", {"table": f"{people}:People", "row": i, "member": people}, line)
                for i, line in enumerate(PEOPLE, start=1)
            ],
        ],
    )
    question = "Veeva CRM Migration Lead Open"
    async with tenant_txn(world["a"]) as session:
        ws = await A.load_workspace(session, "priya-test")
        ws.passages = await A.passage_rows(session, ws, question, {a.id for a in ws.accounts}, None)
        assert ws.passages[0].key == f"passage:{doc}:2"
        await A.take_whole_file(session, ws, 300_000)
    assert len(ws.whole_files) == 1
    whole = ws.whole_files[0]
    assert whole.text.split("\n") == [*POSTINGS, "Notes: open requisitions only"]
    assert all(line not in whole.text for line in PEOPLE)
    assert (
        whole.title
        == "From your files: Holdings_Job_Postings.xlsx in Holdings_workspace.zip (the whole file)"
    )
    assert whole.meta == "Internal · 4 passages, read whole"
    assert whole.extra == {
        "whole_file": True,
        "document": "Holdings_Job_Postings.xlsx in Holdings_workspace.zip",
        "lines": 4,
        "member": sheet,
    }
    # only the member's passages leave the retrieved list; the other sheet's would stay
    assert not any(r.key in {f"passage:{doc}:{i}" for i in (1, 2, 3, 4)} for r in ws.passages)
    files = file_blocks({"f1": whole})
    assert files.startswith(
        f'<file doc="{doc}" name="Holdings_Job_Postings.xlsx in Holdings_workspace.zip">'
    )
    # the budget is the member's size, not the archive's: the whole zip is larger than this
    async with tenant_txn(world["a"]) as session:
        ws = await A.load_workspace(session, "priya-test")
        ws.passages = await A.passage_rows(session, ws, question, {a.id for a in ws.accounts}, None)
        member_chars = sum(len(line) for line in [*POSTINGS, "Notes: open requisitions only"])
        await A.take_whole_file(session, ws, member_chars)
        assert len(ws.whole_files) == 1
        ws.whole_files, ws.passages = (
            [],
            await A.passage_rows(session, ws, question, {a.id for a in ws.accounts}, None),
        )
        await A.take_whole_file(session, ws, member_chars - 1)
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
