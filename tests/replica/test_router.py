"""The Ask router (tabular questions, 2026-10-17): filter questions over a workspace with
tables go to the table tools, prose questions to retrieval, both when the question asks for
both; without a table everything is prose; the model fallback is used only when no rule fires
and its reply must be the schema's JSON.

GUARANTEE(test_the_router_sends_filter_questions_to_the_table_tools)
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.replica import router as R
from documents.tables import ColumnInfo, TableInfo


def _jobs() -> TableInfo:
    cols = [
        ("requisition", "Requisition", "number"),
        ("title", "Title", "text"),
        ("platforms_named", "Platforms named", "text"),
        ("status", "Status", "text"),
        ("first_seen", "First seen", "date"),
        ("full_description", "Full description", "text"),
    ]
    return TableInfo(
        id=uuid4(),
        name="Jobs",
        table="GSK_Job_Postings.xlsx:Jobs",
        member="GSK_Job_Postings.xlsx",
        document="GSK workspace.zip",
        document_id=uuid4(),
        account_id=uuid4(),
        columns=tuple(ColumnInfo(n, r, t, n == "full_description") for n, r, t in cols),
        business_key="requisition",
        row_count=644,
    )


QUESTION = "List all open Salesforce-related jobs first seen on or after March 1, 2026"


@pytest.mark.asyncio
async def test_the_router_sends_filter_questions_to_the_table_tools() -> None:
    tables = [_jobs()]
    got = await R.route(QUESTION, tables)
    assert got.route == "tabular" and "first_seen" in got.reason and "list" in got.reason
    assert (await R.route("How many postings name Veeva?", tables)).route == "tabular"
    assert (await R.route("Which requisitions are still open?", tables)).route == "tabular"
    # a filter question that also asks for a narrative offers both
    mixed = await R.route("Summarise the open Salesforce postings since March", tables)
    assert mixed.route == "mixed" and "prose words" in mixed.reason
    # no table: prose, whatever the words
    none = await R.route(QUESTION, [])
    assert none.route == "prose" and none.reason == "no table in the workspace"
    # no rule fires: the fallback classification decides, and only with a valid route
    seen: list[tuple[str, list[str]]] = []

    async def classify(question: str, hints: Any) -> str | None:
        seen.append((question, list(hints)))
        return "prose"

    got = await R.route("Tell me what GSK's postings say about Veeva.", tables, classify)
    assert got.route == "prose" and "model classification" in got.reason
    assert seen and seen[0][1] == [
        "Jobs: Requisition, Title, Platforms named, Status, First seen, Full description"
    ]

    async def broken(question: str, hints: Any) -> str | None:
        raise RuntimeError("provider down")

    got = await R.route("Tell me what GSK's postings say about Veeva.", tables, broken)
    assert got.route == "mixed" and "no rule fired" in got.reason

    async def nonsense(question: str, hints: Any) -> str | None:
        return "table"

    assert (await R.route("Tell me about it.", tables, nonsense)).route == "mixed"
    # column names count as a rule even without a filter verb
    assert (await R.route("Status of the Salesforce postings?", tables)).route == "tabular"


@pytest.mark.asyncio
async def test_the_model_classifier_accepts_only_the_schema_json() -> None:
    class Reply:
        def __init__(self, text: str) -> None:
            self.content = [type("B", (), {"text": text})()]

    class Messages:
        def __init__(self, text: str) -> None:
            self.text, self.calls = text, []

        async def create(self, **kw: Any) -> Reply:
            self.calls.append(kw)
            return Reply(self.text)

    class Client:
        def __init__(self, text: str) -> None:
            self.messages = Messages(text)

    good = Client('Sure: {"route": "tabular"}')
    classifier = R.LlmRouteClassifier(good, "model-z")
    assert await classifier("q", ["Jobs: Title"]) == "tabular"
    call = good.messages.calls[0]
    assert call["temperature"] == 0 and call["model"] == "model-z"
    assert call["max_tokens"] == 40 and "<question>" in call["messages"][0]["content"]
    assert await R.LlmRouteClassifier(Client('{"route": "maybe"}'), "m")("q", []) is None
    assert await R.LlmRouteClassifier(Client("tabular"), "m")("q", []) is None
    assert await R.LlmRouteClassifier(Client('{"route": "prose", "x": 1}'), "m")("q", []) is None
