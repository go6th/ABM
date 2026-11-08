"""Ask streams its answer sentence by sentence and every streamed citation is a checked row
(SYNTHETIC rows and a fake model).

GUARANTEE(test_a_streamed_answer_only_sends_checked_citations)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.api.routes.ask import (
    RESET_NOTICE,
    _failed_draft,
    _mark_local_fallback,
    with_table_tools,
)
from account_intelligence.replica import answers as A
from account_intelligence.replica import router as R
from account_intelligence.replica.model_answer import (
    HISTORY_TOTAL_CHARS,
    history_messages,
    stream_with_model,
    take,
)
from agents.skill_context import ask_skill_context
from documents.tables import ColumnInfo, TableInfo


@dataclass
class _Event:
    kind: str
    text: str = ""
    usage: Any = None


class FakeProvider:
    """Streams a reply in awkward chunks (citations split across them)."""

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    async def stream(self, *_a: Any, **_k: Any):  # type: ignore[no-untyped-def]
        for c in self.chunks:
            yield _Event("text", c)
        yield _Event("usage", usage=None)


def _world() -> A.Workspace:
    acct = A.Acct(
        id=uuid4(),
        domain="acme.example",
        name="Acme",
        lanes=[],
        cadence=None,
        last_refresh=None,
        refreshes=1,
    )
    now = datetime.now(UTC)
    facts = [
        A.Row(f"fact:{uuid4()}", "fact", acct, t, "Acme news", "Acme", now, None, "confirmed")
        for t in ("Acme is hiring Salesforce developers", "Acme builds a data platform")
    ]
    return A.Workspace([acct], facts, [], [], [], [], {})


async def _collect(chunks: list[str]) -> list[tuple[str, Any]]:
    ws = _world()
    q, local = A.draft_local("What is Acme hiring for?", ws, datetime.now(UTC))
    out = []
    async for kind, value in stream_with_model(
        FakeProvider(chunks), object(), q, ws, local, user_id="u", request_id="r"
    ):
        out.append((kind, value))
    return out


@pytest.mark.asyncio
async def test_a_streamed_answer_only_sends_checked_citations() -> None:
    events = await _collect(
        ["Acme is hiring [R", "1]. It also ", "builds a platform [R2].\n", "\nAsk about it [R1]."]
    )
    pieces = [v for k, v in events if k == "piece"]
    assert [p for p, _ in pieces] == [0, 0, 1]
    assert all("[R" not in text and "[[fact:" in text for _, text in pieces)
    kind, reply = events[-1]
    assert kind == "done" and len(reply.draft.paragraphs) == 2
    numbering: dict[str, int] = {}
    streamed = [A.number_marks(text, numbering) for _, text in pieces]
    final = A.finish(
        reply.draft, A.understand("q", _world(), None), _world(), datetime.now(UTC), None
    )
    # the numbers the stream showed are the numbers of the final answer
    assert [x["v"] for parts in streamed for x in parts if x["t"] == "cite"] == [
        x["v"] for para in final["paragraphs"] for x in para if x["t"] == "cite"
    ]


@pytest.mark.asyncio
async def test_every_answer_carries_the_owners_skill_in_its_system_prompt() -> None:
    seen: dict[str, Any] = {}

    class Capture(FakeProvider):
        async def stream(self, *a: Any, **k: Any):  # type: ignore[no-untyped-def]
            seen["system"] = k["system"]
            async for event in super().stream(*a, **k):
                yield event

    ws = _world()
    q, local = A.draft_local("What is Acme hiring for?", ws, datetime.now(UTC))
    async for _ in stream_with_model(
        Capture(["Acme is hiring [R1]."]), object(), q, ws, local, user_id="u", request_id="r"
    ):
        pass
    assert seen["system"].endswith(ask_skill_context())
    assert "## 5. Answering questions against an existing workspace" in seen["system"]


@pytest.mark.asyncio
async def test_the_file_map_rides_in_the_cached_block_and_the_shared_part_stays_first() -> None:
    seen: dict[str, Any] = {}

    class Capture(FakeProvider):
        async def stream(self, *a: Any, **k: Any):  # type: ignore[no-untyped-def]
            seen["system"], seen["cached"] = k["system"], k["cached"]
            async for event in super().stream(*a, **k):
                yield event

    ws = _world()
    q, local = A.draft_local("What is Acme hiring for?", ws, datetime.now(UTC))
    index = '<workspace_files>\nTABLE "Jobs"\n</workspace_files>'
    async for _ in stream_with_model(
        Capture(["Acme is hiring [R1]."]),
        object(),
        q,
        ws,
        local,
        user_id="u",
        request_id="r",
        file_map=index,
    ):
        pass
    system = seen["system"]
    # the first part (rules and skill) stays identical, so its cache entry is shared; the map
    # is workspace-stable, so it rides in the cached block (with the whole files), not in the
    # block that the route's rules and the question's tables make vary
    assert system.parts[0].endswith(ask_skill_context())
    assert seen["cached"] == index and index not in str(system)


@pytest.mark.asyncio
async def test_an_invented_citation_is_dropped_but_a_list_or_table_streams() -> None:
    invented = await _collect(["Acme is hiring [R1]. ", "Also [R9] says more. ", "End."])
    # the sentence resting only on a row that was not sent is dropped, never streamed, and
    # the paid reply is kept (token review item 6, 2026-10-20: was a reset to the local answer)
    assert [k for k, _ in invented] == ["piece", "piece", "done"]
    assert all("R9" not in text and "says more" not in text for _, (_, text) in invented[:2])
    assert "says more" not in invented[-1][1].draft.paragraphs[0]
    # Markdown structure is kept line for line (owner decision 2026-10-17)
    listed = await _collect(["- Acme is hiring [R1].\n- It builds [R2].\n\nMore [R2]."])
    # each list line is its own checked piece (a sentence end), in the same paragraph
    assert [k for k, _ in listed] == ["piece", "piece", "piece", "done"]
    assert [p for p, _ in listed[:3] if True] and [v[0] for k, v in listed[:3]] == [0, 0, 1]
    kind, reply = listed[-1]
    assert kind == "done"
    assert reply.draft.paragraphs[0].startswith("- Acme is hiring")
    assert "\n- It builds" in reply.draft.paragraphs[0]
    table = await _collect(
        ["| Role | Seen |\n|---|---|\n| Architect | Jun [R1] |\n| Lead | Jul [R2] |"]
    )
    assert [k for k, _ in table] == ["piece", "done"]
    assert table[-1][1].draft.paragraphs[0].count("\n") == 3


def test_pieces_wait_for_the_citations_after_a_sentence() -> None:
    assert take("Acme is hiring. ") == (None, False, "Acme is hiring. ")
    assert take("Acme is hiring. [R1] Next") == ("Acme is hiring. [R1] ", False, "Next")
    assert take("Last one", final=True) == ("Last one", True, "")


def test_model_failure_keeps_local_draft_and_adds_a_fallback_notice() -> None:
    draft = A.Draft()
    draft.para("Acme is hiring Salesforce developers [[fact:1]].")

    _mark_local_fallback(draft)

    assert draft.paragraphs == [
        "AI summary unavailable. Showing workspace records:",
        "Acme is hiring Salesforce developers [[fact:1]].",
    ]


def test_a_discarded_model_reply_is_announced_not_shown_as_the_answer() -> None:
    """2026-11-03: the notice used to sit above the local placeholder rows ("At UCB Inc, UCB
    [1]"), which still read as an answer; a paid reply that was discarded now shows the notice
    alone, keeps the account in focus and cites nothing."""
    draft = A.Draft()
    draft.para("At Acme, Acme [[fact:1]].")
    draft.rows = {"fact:1": object()}  # type: ignore[dict-item]

    out = _failed_draft(draft)

    assert out.paragraphs == [RESET_NOTICE] and out.rows == {}
    assert out.focus is draft.focus and out.structured is None
    assert "no analysis" in RESET_NOTICE and "Showing workspace records" not in RESET_NOTICE


def test_history_alternates_drops_citation_marks_and_keeps_the_newest_within_the_budget() -> None:
    turns = [(f"question {i}", f"answer {i} [1][2] ends") for i in range(8)]
    got = history_messages(turns)
    assert [m.role for m in got] == ["user", "assistant"] * 6  # HISTORY_TURNS newest
    assert got[0].content == "question 2" and got[-1].content == "answer 7 ends"
    assert history_messages([("q", ""), ("", "a")]) == []
    big = history_messages([("old", "x" * 3000)] * 3 + [("new", "y" * 3000)] * 3)
    assert sum(len(m.content) for m in big) <= HISTORY_TOTAL_CHARS
    assert big[-2].content == "new"  # the newest turn is always the one kept


@pytest.mark.asyncio
async def test_earlier_turns_go_to_the_model_before_the_question() -> None:
    seen: dict[str, Any] = {}

    class Capture(FakeProvider):
        async def stream(self, *a: Any, **k: Any):  # type: ignore[no-untyped-def]
            seen["messages"] = a[1]
            async for event in super().stream(*a, **k):
                yield event

    ws = _world()
    q, local = A.draft_local("What about Petaluma?", ws, datetime.now(UTC))
    async for _ in stream_with_model(
        Capture(["Acme is hiring [R1]."]),
        object(),
        q,
        ws,
        local,
        user_id="u",
        request_id="r",
        history=[("Which people are in the Bay Area?", "Four people [1].")],
    ):
        pass
    roles = [m.role for m in seen["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert seen["messages"][1].content == "Four people."
    assert seen["messages"][2].content.startswith("<conversation>")
    assert "What about Petaluma?" in seen["messages"][2].content


def test_tables_in_the_workspace_always_give_the_model_the_table_tools() -> None:
    table = TableInfo(
        id=uuid4(),
        name="People",
        table="People_List.xlsx:People",
        member=None,
        document="People_List.xlsx",
        document_id=uuid4(),
        account_id=uuid4(),
        columns=(ColumnInfo("location", "Location", "text", False),),
        business_key=None,
        row_count=1620,
    )
    prose = R.Decision("prose", "research tables only; no filter word or table named")
    upgraded = with_table_tools(prose, [table])
    assert upgraded.route == "mixed" and "tables exist" in upgraded.reason
    assert with_table_tools(prose, []) is prose  # nothing to compute over: retrieval only
    tabular = R.Decision("tabular", "columns ['location']")
    assert with_table_tools(tabular, [table]) is tabular


@pytest.mark.asyncio
async def test_own_knowledge_is_cited_as_model_knowledge_and_is_no_workspace_answer() -> None:
    events = await _collect(
        ["Berkeley is in Alameda County, part of the Bay Area [K]. Acme is hiring [R1]."]
    )
    kind, reply = events[-1]
    assert kind == "done"
    ws = _world()
    final = A.finish(reply.draft, A.understand("q", ws, None), ws, datetime.now(UTC), None)
    kinds = [s["kind"] for s in final["sources"]]
    assert "knowledge" in kinds and final["answered"] is True
    knowledge = next(s for s in final["sources"] if s["kind"] == "knowledge")
    assert knowledge["title"] == "Model knowledge" and knowledge["url"] is None

    only = await _collect(["The Bay Area has nine counties [K]."])
    kind, reply = only[-1]
    assert kind == "done"
    final = A.finish(reply.draft, A.understand("q", ws, None), ws, datetime.now(UTC), None)
    # knowledge alone is an answer, but not one from the workspace
    assert final["answered"] is False and final["used"]["workspace"] == "not_used"
    assert [s["kind"] for s in final["sources"]] == ["knowledge"]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_threads_history_is_its_own_completed_model_turns_oldest_first(
    world: dict[str, Any],
) -> None:
    from datetime import date

    from account_intelligence.api.routes.ask import LOCAL_MODEL
    from account_intelligence.chat import store
    from account_intelligence.db.session import tenant_txn
    from llm.choice import catalogue

    tenant, user = world["a"], f"hist-{uuid4().hex[:6]}"
    paid = catalogue().ids[3]  # any priced model id (R1: no model id in tests)
    thread = None
    async with tenant_txn(tenant, user=user) as s:
        for model, question, answer in (
            (paid, "first", "one"),
            (LOCAL_MODEL, "second", "placeholder"),
            (paid, "third", "three"),
        ):
            thread, _, _ = await store.record_ask_turn(
                s,
                user_id=user,
                thread_id=thread,
                request_id=uuid4(),
                model_id=model,
                question=question,
                plain_answer=answer,
                account_id=None,
                input_tokens=1,
                output_tokens=1,
                answer_json="{}",
                week=date(2026, 11, 2),
                category="hiring",
            )
    async with tenant_txn(tenant, user=user) as s:
        got = await store.ask_history(s, thread_id=thread, limit=6, skip_model_id=LOCAL_MODEL)
        capped = await store.ask_history(s, thread_id=thread, limit=1, skip_model_id=LOCAL_MODEL)
    assert got == [("first", "one"), ("third", "three")]
    assert capped == [("third", "three")]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_route_streams_text_that_matches_the_final_answer(world: dict[str, Any]) -> None:
    from tests.replica.conftest import _call, _plane

    plane = _plane(world["a"], "priya-test", world["payloads"])
    r = await _call(
        plane,
        "POST",
        "/v1/chat/ask",
        json={"request_id": str(uuid4()), "question": "Who are the people at Highmark?"},
    )
    frames = []
    for raw in r.text.strip().split("\n\n"):
        lines = raw.split("\n")
        frames.append(
            (lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: ")))
        )
    kinds = [k for k, _ in frames]
    assert kinds.index("text") < kinds.index("answer")
    streamed: dict[int, list[dict[str, str]]] = {}
    for k, data in frames:
        if k == "text":
            streamed.setdefault(data["p"], []).extend(data["parts"])
    answer = next(d for k, d in frames if k == "answer")

    def flat(parts: list[dict[str, str]]) -> str:
        return "".join(f"[{p['v']}]" if p["t"] == "cite" else p["v"] for p in parts).strip()

    assert [flat(streamed[i]) for i in sorted(streamed)] == [flat(p) for p in answer["paragraphs"]]


@pytest.mark.asyncio
async def test_a_silent_ask_stream_sends_keepalives_and_keeps_every_frame() -> None:
    import asyncio

    from account_intelligence.api.routes.ask import KEEPALIVE, _with_keepalive

    async def source():  # type: ignore[no-untyped-def]
        yield b"event: status\ndata: {}\n\n"
        await asyncio.sleep(0.05)  # the model is writing: nothing is sent
        yield b"event: answer\ndata: {}\n\n"

    got = [f async for f in _with_keepalive(source(), every=0.01)]
    assert got[0] == b"event: status\ndata: {}\n\n" and got[-1] == b"event: answer\ndata: {}\n\n"
    assert KEEPALIVE in got[1:-1]
    # a comment frame carries no data line, so an SSE reader skips it
    assert KEEPALIVE.startswith(b":") and b"data:" not in KEEPALIVE

    async def broken():  # type: ignore[no-untyped-def]
        yield b"event: status\ndata: {}\n\n"
        raise RuntimeError("source failed")

    with pytest.raises(RuntimeError, match="source failed"):
        [f async for f in _with_keepalive(broken(), every=0.01)]


@pytest.mark.asyncio
async def test_a_table_read_whole_by_the_plan_rides_in_the_last_system_block_and_the_prompt() -> (
    None
):
    seen: dict[str, Any] = {}

    class Capture(FakeProvider):
        async def stream(self, *a: Any, **k: Any):  # type: ignore[no-untyped-def]
            seen["cached"], seen["system"], seen["prompt"] = (
                k["cached"],
                k["system"],
                a[1][0].content,
            )
            async for event in super().stream(*a, **k):
                yield event

    ws = _world()
    acct = ws.accounts[0]
    table = A.Row(
        "table:5c1b4f5e-0000-4000-8000-000000000001",
        "file",
        acct,
        "Jobs.xlsx, table Jobs: 2 rows; columns: title",
        "From your files: Jobs.xlsx (table Jobs)",
        "Internal",
        None,
        None,
        "internal",
        extra={
            "table": "Jobs",
            "document": "Jobs.xlsx",
            "rows": 2,
            "whole_text": "row | title\n1 | A\n2 | B",
        },
    )
    ws.tables = [table]
    q, local = A.draft_local("List the jobs", ws, datetime.now(UTC))
    async for _ in stream_with_model(
        Capture(["Two jobs [R1]."]),
        object(),
        q,
        ws,
        local,
        user_id="u",
        request_id="r",
        pre_read="<pre_read>\ncolumns\n</pre_read>",
    ):
        pass
    # every row of the table is in the LAST system block (with the route's rules), labelled by the
    # table's id; the cached block (files and map) does not change with the question's tables
    assert (
        '<file doc="5c1b4f5e-0000-4000-8000-000000000001" name="Jobs">' in seen["system"].parts[-1]
    )
    assert "1 | A\n2 | B" in seen["system"].parts[-1]
    assert seen["cached"] is None
    assert seen["prompt"].startswith("<pre_read>\ncolumns\n</pre_read>")
    assert 'every row also given whole in <file doc="5c1b4f5e' in seen["prompt"]
