"""The Ask router (tabular questions, 2026-10-17): filter questions over a workspace with
tables go to the table tools, prose questions to retrieval, both when the question asks for
both; without a table everything is prose; the model fallback is used only when no rule fires
and its reply must be the schema's JSON. Natural phrasing matches because the tables carry the
lexicon ingest generated: each column's aliases and each low-cardinality column's values.

GUARANTEE(test_the_router_sends_filter_questions_to_the_table_tools)

GUARANTEE(test_natural_phrasing_routes_tabular_or_mixed_and_a_summary_stays_prose)
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.replica import router as R
from documents.chunking import column_aliases
from documents.tables import ColumnInfo, TableInfo


def _jobs() -> TableInfo:
    cols = [
        ("requisition", "Requisition", "number", ()),
        ("title", "Title", "text", ()),
        ("platforms_named", "Platforms named", "text", ("Salesforce", "Veeva", "SAP CRM")),
        ("status", "Status", "text", ("Open", "Closed")),
        ("category", "Category", "text", ("Platform owner", "User role", "Admin")),
        ("first_seen", "First seen", "date", ()),
        ("full_description", "Full description", "text", ()),
    ]
    return TableInfo(
        id=uuid4(),
        name="Jobs",
        table="Holdings_Job_Postings.xlsx:Jobs",
        member="Holdings_Job_Postings.xlsx",
        document="Holdings workspace.zip",
        document_id=uuid4(),
        account_id=uuid4(),
        columns=tuple(
            ColumnInfo(
                n,
                r,
                t,
                n == "full_description",
                aliases=tuple(column_aliases(r, n, t)),
                values=v,
            )
            for n, r, t, v in cols
        ),
        business_key="requisition",
        row_count=644,
    )


QUESTION = "List all open Salesforce-related jobs first seen on or after March 1, 2026"


@pytest.mark.asyncio
async def test_the_router_sends_filter_questions_to_the_table_tools() -> None:
    tables = [_jobs()]
    got = await R.route(QUESTION, tables)
    assert got.route == "tabular" and "first_seen" in got.reason and "list" in got.reason
    assert "first_seen" in got.matched_columns and "open" in got.matched_values
    got = await R.route("How many postings name Veeva?", tables)
    assert got.route == "tabular" and "veeva" in got.matched_values
    assert (await R.route("Which requisitions are still open?", tables)).route == "tabular"
    # a filter question that also asks for a narrative offers both
    mixed = await R.route("Summarise the open Salesforce postings since March", tables)
    assert mixed.route == "mixed" and "prose words" in mixed.reason
    # no table: prose, whatever the words
    none = await R.route(QUESTION, [])
    assert none.route == "prose" and none.reason == "no table in the workspace"
    # column names count as a rule even without a filter verb
    assert (await R.route("Status of the Veeva postings?", tables)).route == "tabular"
    # no rule fires: the fallback classification decides, and only with a valid route
    seen: list[tuple[str, list[str]]] = []

    async def classify(question: str, hints: Any) -> str | None:
        seen.append((question, list(hints)))
        return "prose"

    got = await R.route("Anything interesting lately?", tables, classify)
    assert got.route == "prose" and "model classification" in got.reason
    assert seen and seen[0][1] == [
        "Jobs: Requisition, Title, Platforms named, Status, Category, First seen, Full description"
    ]

    async def broken(question: str, hints: Any) -> str | None:
        raise RuntimeError("provider down")

    got = await R.route("Anything interesting lately?", tables, broken)
    assert got.route == "mixed" and "no rule fired" in got.reason

    async def nonsense(question: str, hints: Any) -> str | None:
        return "table"

    got = await R.route("Anything interesting lately?", tables, nonsense)
    assert got.route == "mixed"


# the five live eval questions of evals/tabular_synthetic (2026-10-17): each must reach the table
# tools, and a pure summary question must stay on retrieval
EVAL_QUESTIONS = (
    "List out all the open jobs that are Salesforce-related that was first seen date on or"
    " after March 1, 2026",
    "Pull the full 40-row Salesforce table, listing every row",
    "Are the duplicate Salesforce Administrator Lead rows separate requisitions?",
    "Which Salesforce roles in the last 12 months are platform owners?",
    "How many Synthetic Holdings postings mention Salesforce, and how many are field-sales"
    " false positives?",
)


@pytest.mark.asyncio
async def test_natural_phrasing_routes_tabular_or_mixed_and_a_summary_stays_prose() -> None:
    tables = [_jobs()]
    for question in EVAL_QUESTIONS:
        got = await R.route(question, tables)
        assert got.route in ("tabular", "mixed"), (question, got)
        assert got.route == "tabular", (question, got.reason)
    summary = await R.route("Summarise the Synthetic Holdings account brief", tables)
    assert summary.route == "prose" and "prose words" in summary.reason
    # the natural forms that carry the eval questions: the header as written, the date
    # column's "... date" form, the low-cardinality values
    first = await R.route(EVAL_QUESTIONS[0], tables)
    assert "first_seen" in first.matched_columns and "open" in first.matched_values
    owners = await R.route(EVAL_QUESTIONS[3], tables)
    assert "platform owner" in owners.matched_values  # plural "owners" matches the value


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
    assert call["model"] == "model-z" and call["max_tokens"] == 64
    # a one-word label: thinking off, the schema as structured output (token review 2026-10-20)
    assert call["thinking"] == {"type": "disabled"}
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "<question>" in call["messages"][0]["content"]
    # no unsupported kwarg may reach the Messages call: the installed SDK's create has NO
    # ``temperature`` parameter (OI-LLM-1), and the one this used to send made every
    # classification TypeError and silently fall back to ``mixed`` (fixed 2026-10-17)
    assert "temperature" not in call
    import inspect

    import anthropic

    accepted = set(inspect.signature(anthropic.resources.messages.AsyncMessages.create).parameters)
    assert set(call) <= accepted
    assert await R.LlmRouteClassifier(Client('{"route": "maybe"}'), "m")("q", []) is None
    assert await R.LlmRouteClassifier(Client("tabular"), "m")("q", []) is None
    assert await R.LlmRouteClassifier(Client('{"route": "prose", "x": 1}'), "m")("q", []) is None
