"""The golden GSK question end to end (tabular questions, 2026-10-17), against the SYNTHETIC
workbook of ``tests.documents.fixtures`` (the owner's real file is not in the repository): the
router sends it to the table tools; a model that follows the tabular rules (scripted here: no
live model call) describes the table, queries it with the three predicates, finds nothing,
relaxes the status alone, deduplicates on the requisition and names the false positives; the
answer is the same, byte for byte, five runs over.

GUARANTEE(test_the_golden_gsk_question_answers_identically_five_times)
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from account_intelligence.chat.service import ChatEvent, ChatUsage
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import answers as A
from account_intelligence.replica import router as R
from account_intelligence.replica.model_answer import TABULAR_RULES, stream_with_model
from account_intelligence.replica.tools import TableTools
from documents import tables as T
from tests.conftest import _make_account
from tests.documents import fixtures
from tests.documents.world import service, upload

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

QUESTION = "List all open Salesforce-related jobs first seen on or after March 1, 2026"
STRICT = [
    {"column": "platforms_named", "op": "icontains", "value": "salesforce"},
    {"column": "status", "op": "eq", "value": "Open"},
    {"column": "first_seen", "op": "gte", "value": "2026-03-01"},
]
SHOWN = ["requisition", "title", "class", "status", "first_seen", "platform_sentences"]
FALSE_POSITIVE = re.compile(r"field sales", re.I)


class ScriptedTabularModel:
    """A model that does what ``TABULAR_RULES`` ask: describe, query, relax one predicate,
    deduplicate, split false positives by the evidence column, cite rows."""

    supports_tools = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.text = ""

    async def stream(self, model: Any, messages: Any, max_tokens: int, **kw: Any) -> Any:
        assert kw["temperature"] == 0.0 and TABULAR_RULES in kw["system"]
        assert [t["name"] for t in kw["tools"]] == ["describe_table", "query_table", "read_rows"]
        prompt = messages[0].content
        rid = re.search(r"\[(R\d+)\][^\n]*\| table \|", prompt).group(1)  # type: ignore[union-attr]
        table = re.search(r'table "([^"]+)"', prompt).group(1)  # type: ignore[union-attr]
        run = kw["run_tool"]
        cite = f"[{rid}]"
        payload: dict[str, Any] = {"table": table}
        self.calls.append({"name": "describe_table", "input": payload})
        described = await run("describe_table", payload)
        yield ChatEvent("tool", tool={"name": "describe_table"})
        assert {c["name"] for c in described["columns"]} >= {"requisition", "status"}
        key = described["business_key"]
        payload = {"table": table, "where": STRICT, "distinct_on": key, "columns": SHOWN}
        self.calls.append({"name": "query_table", "input": payload})
        strict = await run("query_table", payload)
        yield ChatEvent("tool", tool={"name": "query_table"})
        lines = [
            "**GSK**: no open Salesforce-related posting was first seen on or after 1 March"
            f" 2026: the filter returns 0 rows (none) {cite}."
        ]
        if strict["row_count"] == 0:
            yield ChatEvent("text", text="Checking the sheet. One moment")
            yield ChatEvent("discard")  # the protocol: text before a tool call is withdrawn
            payload = {
                "table": table,
                "where": [w for w in STRICT if w["column"] != "status"],
                "distinct_on": key,
                "columns": SHOWN,
                "order_by": [{"column": "first_seen", "desc": True}],
            }
            self.calls.append({"name": "query_table", "input": payload})
            relaxed = await run("query_table", payload)
            yield ChatEvent("tool", tool={"name": "query_table"})
            rows = relaxed["rows"]
            genuine = [
                r
                for r in rows
                if not FALSE_POSITIVE.search(str(r["cells"].get("platform_sentences") or ""))
            ]
            false = [r for r in rows if r not in genuine]
            total = relaxed["row_count"] + relaxed["dedup_removed"]
            lines.append(
                f"Relaxing the status predicate (the date stays) finds {total} rows,"
                f" {relaxed['row_count']} unique postings after deduplicating on"
                f" {relaxed['deduped_on']} ({relaxed['dedup_removed']} duplicate rows removed),"
                f" all Closed {cite}."
            )
            lines.append(f"Genuine Salesforce platform postings: {len(genuine)} {cite}.")
            lines.append(
                "| Row | Requisition | Title | Class | First seen | Source |\n"
                "|---|---|---|---|---|---|"
            )
            for r in genuine:
                c = r["cells"]
                lines.append(
                    f"| row {r['row']} | {c['requisition'] or '(none)'} | {c['title']} |"
                    f" {c['class']} | {c['first_seen']} | {cite} |"
                )
            lines.append(
                "False positives, where salesforce means the field sales team:"
                f" {len(false)} {cite}."
            )
            for r in false:
                c = r["cells"]
                phrase = re.search(r"[^.]*field sales[^.]*", str(c["platform_sentences"]), re.I)
                quoted = phrase.group(0).strip() if phrase else ""
                lines.append(
                    f'- {c["requisition"]} {c["title"]} (row {r["row"]}): "{quoted}" {cite}'
                )
        self.text = "\n\n".join(lines)
        for i in range(0, len(self.text), 37):
            yield ChatEvent("text", text=self.text[i : i + 37])
        yield ChatEvent("usage", usage=ChatUsage(1, 1))


async def _run_once(
    tenant: UUID, account: UUID, acct: A.Acct
) -> tuple[list[dict[str, Any]], dict[str, Any], str, list[tuple[str, Any]]]:
    ws = A.Workspace([acct], [], [], [], [], [], [], {}, False)
    async with tenant_txn(tenant, user="user-1") as s:
        found = await T.list_tables(s, [account])
    decision = await R.route(QUESTION, found)
    assert decision.route == "tabular", decision
    ws.tables = A.table_rows(ws, found)
    tools = TableTools(lambda: tenant_txn(tenant, user="user-1"), found)
    q, local = A.draft_local(QUESTION, ws, datetime.now(UTC))
    model = ScriptedTabularModel()
    events = [
        (k, v)
        async for k, v in stream_with_model(
            model,
            object(),
            q,
            ws,
            local,
            user_id="u",
            request_id="r",
            tools=tools.tools,
            run_tool=tools.run,
            temperature=0.0,
        )
    ]
    return model.calls, tools.stats(), model.text, events


async def test_the_golden_gsk_question_answers_identically_five_times(
    tenant_a: UUID, tmp_path: Path
) -> None:
    domain = f"gsk-{uuid4().hex[:6]}.example"
    account = _make_account(tenant_a, domain)
    svc = service(tmp_path)
    done = await upload(
        svc, tenant_a, "GSK_Job_Postings.xlsx", fixtures.gsk_job_postings_xlsx(), account
    )
    assert await svc.process(tenant_a, UUID(done["document_id"])) == "indexed"
    acct = A.Acct(account, domain, "GSK", [], None, None, 1)
    runs = [await _run_once(tenant_a, account, acct) for _ in range(5)]
    calls, stats, text, events = runs[0]
    # the tool calls: describe, the three predicates, then the status alone relaxed
    assert [c["name"] for c in calls] == ["describe_table", "query_table", "query_table"]
    assert (
        calls[1]["input"]["where"] == STRICT and calls[1]["input"]["distinct_on"] == "requisition"
    )
    assert [w["column"] for w in calls[2]["input"]["where"]] == ["platforms_named", "first_seen"]
    assert stats["tools_called"] == 3 and stats["relaxed_predicate"] == "status"
    assert stats["predicates"] == (
        "platforms_named:icontains,status:eq,first_seen:gte,platforms_named:icontains,first_seen:gte"
    )
    assert stats["row_count"] == 14 and stats["dedup_removed"] == 6 and stats["empty_results"] == 1
    # the answer: 0 open, 14 unique of 20, four owner-class, the false positives named
    assert "0 rows (none)" in text and "finds 20 rows, 14 unique postings" in text
    assert text.count("| row ") == 8
    assert "False positives, where salesforce means the field sales team: 6" in text
    for req in fixtures.GSK_FALSE_POSITIVES:
        assert f"- {req} " in text
    titles = [t for _, t, *_ in fixtures.GSK_SALESFORCE_RECENT]
    assert sum(1 for t in titles if t in text) >= 14
    owner_rows = [
        line
        for line in text.splitlines()
        if line.startswith("| row ") and any(k in line for k in fixtures.OWNER_CLASSES)
    ]
    assert len(owner_rows) == 4
    # the stream: tool events, the withdrawn preface, checked pieces citing the table, the reply
    kinds = [k for k, _ in events]
    assert kinds[:4] == ["tool", "tool", "piece", "discard"] and kinds[4] == "tool"
    assert kinds[-1] == "done" and kinds.count("discard") == 1
    reply = events[-1][1]
    paragraphs = "\n".join(reply.draft.paragraphs)
    assert "[[table:" in paragraphs and "Checking the postings" not in paragraphs
    assert reply.draft.paragraphs[0].startswith("**GSK**: no open Salesforce-related posting")
    # five runs: byte-identical tool calls, statistics and answer text
    for other in runs[1:]:
        assert json.dumps(other[0], sort_keys=True) == json.dumps(calls, sort_keys=True)
        assert other[1] == stats and other[2] == text
