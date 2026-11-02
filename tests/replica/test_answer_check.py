"""The answer check of a tabular turn: ``render_answer`` before any table was read, an answer that
says the data is missing, and a table that lists a fraction of the rows the last query matched are
each sent back once or twice, then the call stands (Ask redesign 3a, 2026-11-02).

GUARANTEE(test_a_tabular_answer_before_any_read_is_sent_back_then_stands)
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from account_intelligence.platform.llm import ask_procedure_models
from account_intelligence.replica import tools as TL
from account_intelligence.replica.model_answer import PROCEDURE_RULES, stream_with_model


def _tools(**kw: Any) -> TL.AccessTools:
    return TL.AccessTools(lambda: None, [], [], None, **kw)  # type: ignore[arg-type,return-value]


def _answer(rows: int = 3, headline: str = "Three postings match") -> dict[str, Any]:
    return {
        "headline": headline,
        "sections": [
            {
                "kind": "table",
                "columns": ["Title", "Cite"],
                "rows": [[f"Job {i}", "[R1]"] for i in range(rows)],
            }
        ],
    }


@pytest.mark.asyncio
async def test_a_tabular_answer_before_any_read_is_sent_back_then_stands() -> None:
    tools = _tools(require_data=True)
    for _ in range(TL.MAX_READ_REFUSALS):
        out = await tools.run("render_answer", _answer())
        assert out["error"]["code"] == "read_first" and "describe_table" in out["error"]["message"]
    assert tools.rendered is None
    # the refusals are spent: the call stands rather than looping the turn forever
    assert (await tools.run("render_answer", _answer()))["terminal"] is True
    assert tools.stats()["read_refusals"] == TL.MAX_READ_REFUSALS


@pytest.mark.asyncio
async def test_a_turn_that_read_a_table_is_not_sent_back_and_other_routes_are_never_checked() -> (
    None
):
    read = _tools(require_data=True)
    read.data_calls = 1
    assert (await read.run("render_answer", _answer()))["terminal"] is True
    assert (await _tools().run("render_answer", _answer()))["terminal"] is True  # not tabular


@pytest.mark.asyncio
async def test_an_answer_that_says_the_data_is_missing_is_sent_back() -> None:
    tools = _tools(require_data=True)
    tools.data_calls = 1
    out = await tools.run("render_answer", _answer(headline="Please re-upload the file"))
    assert out["error"]["code"] == "files_present"
    assert (await tools.run("render_answer", _answer()))["terminal"] is True


@pytest.mark.asyncio
async def test_a_table_listing_a_fraction_of_the_matched_rows_is_sent_back_once() -> None:
    tools = _tools(require_data=True)
    tools.data_calls = 1
    tools.table_tools.last_query = (40, False)
    out = await tools.run("render_answer", _answer(rows=5))
    assert out["error"]["code"] == "rows_missing"
    assert "5 rows" in out["error"]["message"] and "matched 40" in out["error"]["message"]
    assert (await tools.run("render_answer", _answer(rows=5)))["terminal"] is True  # once only
    assert tools.stats()["shortfall_refusals"] == 1


@pytest.mark.asyncio
async def test_a_cut_or_small_query_is_never_compared() -> None:
    cut = _tools(require_data=True)
    cut.data_calls = 1
    cut.table_tools.last_query = (400, True)  # truncated at its limit: the table may be shorter
    assert (await cut.run("render_answer", _answer(rows=2)))["terminal"] is True
    small = _tools(require_data=True)
    small.data_calls = 1
    small.table_tools.last_query = (4, False)
    assert (await small.run("render_answer", _answer(rows=1)))["terminal"] is True


@pytest.mark.asyncio
async def test_the_reading_procedure_goes_to_the_listed_models_only() -> None:
    from account_intelligence.replica import answers as A
    from tests.replica.test_ask_stream import FakeProvider, _world

    seen: dict[str, Any] = {}

    class Capture(FakeProvider):
        async def stream(self, *a: Any, **k: Any):  # type: ignore[no-untyped-def]
            seen["system"] = str(k["system"])
            async for event in super().stream(*a, **k):
                yield event

    class Model:
        def __init__(self, id: str) -> None:
            self.id = id

    tool_defs = [{"name": "query_table"}, {"name": "render_answer"}]
    listed = sorted(ask_procedure_models())[0]  # a model id comes from config, never a literal
    for model_id, expected in ((listed, True), ("not-a-listed-model", False)):
        ws = _world()
        from datetime import UTC, datetime

        q, local = A.draft_local("What is Acme hiring for?", ws, datetime.now(UTC))
        async for _ in stream_with_model(
            Capture(["Acme is hiring [R1]."]),
            Model(model_id),
            q,
            ws,
            local,
            user_id="u",
            request_id="r",
            tools=tool_defs,
        ):
            pass
        assert (PROCEDURE_RULES in seen["system"]) is expected, model_id


@pytest.mark.asyncio
async def test_a_failing_call_is_not_a_read_and_a_refusal_word_in_a_cell_is_not_a_refusal() -> None:
    tools = _tools(require_data=True)
    # a call that returned an error (unknown table, unknown tool) did not read anything
    tools.table_tools.tables = []
    out = await tools.run("describe_table", {"table": "nope"})
    assert "error" in out and tools.data_calls == 0
    assert (await tools.run("no_such_tool", {}))["error"]["code"] == "unknown_tool"
    assert tools.data_calls == 0
    tools.data_calls = 1
    # a posting titled "re-export" in a table cell is data, not the answer saying a file is missing
    cell_says = {
        "headline": "One posting",
        "sections": [
            {"kind": "table", "columns": ["Title"], "rows": [["Data re-export analyst [R1]"]]}
        ],
    }
    assert (await tools.run("render_answer", cell_says))["terminal"] is True
    # but the same words in what the answer says are refused, curly apostrophes included
    spoken = tools.__class__(lambda: None, [], [], None, require_data=True)  # type: ignore[arg-type]
    spoken.data_calls = 1
    out = await spoken.run("render_answer", _answer(headline="I can’t access the file"))
    assert out["error"]["code"] == "files_present"


@pytest.mark.asyncio
async def test_split_tables_are_summed_and_a_result_section_needs_no_comparison() -> None:
    split = _tools(require_data=True)
    split.data_calls = 1
    split.table_tools.last_query = (100, False)
    three = {
        "headline": "100 rows",
        "sections": [
            {"kind": "table", "columns": ["T"], "rows": [[f"a{i}"] for i in range(34)]},
            {"kind": "table", "columns": ["T"], "rows": [[f"b{i}"] for i in range(33)]},
            {"kind": "table", "columns": ["T"], "rows": [[f"c{i}"] for i in range(33)]},
        ],
    }
    assert (await split.run("render_answer", three))["terminal"] is True  # 100 listed in all
    # a result section is complete by construction, whatever a later unrelated query matched
    result = _tools(require_data=True)
    result.data_calls = 1
    result.table_tools.last_query = (500, False)
    result.table_tools.stored["q1"] = (
        SimpleNamespace(name="T", row_count=3, id="id", columns=[], names={}),
        SimpleNamespace(columns=[]),
        {"rows": [], "row_count": 3, "applied_where": []},
    )
    out = await result.run(
        "render_answer", {"headline": "x", "sections": [{"kind": "result", "result_id": "q1"}]}
    )
    assert out["terminal"] is True


def test_a_result_too_large_for_the_tool_limit_still_names_its_result() -> None:
    from account_intelligence.chat.service import TOOL_RESULT_MAX_CHARS, _tool_result_text

    big = {
        "result_id": "q3",
        "row_count": 900,
        "rows": ["x" * 500] * (TOOL_RESULT_MAX_CHARS // 400),
    }
    stub = json.loads(_tool_result_text(big))
    assert stub["error"]["code"] == "result_too_large" and stub["result_id"] == "q3"
    assert stub["row_count"] == 900 and "result section" in stub["hint"]
