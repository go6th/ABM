"""``render_answer``: the model's final answer as strict JSON, checked and numbered by the
code (owner decision 2026-10-17 — the answer's format no longer depends on which model ran).
A call is shape-checked by the access tools (they see no workspace rows), citation-checked by
``stream_with_model`` against the numbered rows exactly as Markdown is, ends the provider's
tool loop (``terminal``), and becomes ``Draft.structured`` — the client renders the cards and
tables; the same answer also exists as Markdown paragraphs for the stream and the store. The
post-processor guarantee: no ``[[…]]`` row marker ever reaches an answer, streamed or stored
(passage keys carry a ``:<seq>`` tail and used to leak right through the markers' regex)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.chat.service import (
    AnthropicChatProvider,
    ChatEvent,
    ChatMessage,
    ChatUsage,
)
from account_intelligence.replica import answers as A
from account_intelligence.replica import render as RENDER
from account_intelligence.replica.model_answer import (
    TABULAR_RULES,
    context_block,
    pick_rows,
    stream_with_model,
)
from account_intelligence.replica.tools import AccessTools

NOW = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)


def _acct() -> A.Acct:
    return A.Acct(
        id=uuid4(),
        domain="highmark.example",
        name="Highmark",
        lanes=["Salesforce"],
        cadence="weekly",
        last_refresh=NOW - timedelta(days=2),
        refreshes=2,
    )


def _row(
    a: A.Acct, text: str, *, key: str | None = None, kind: str = "fact", days: int = 3
) -> A.Row:
    return A.Row(
        key=key or f"{kind}:{uuid4()}",
        kind=kind,
        account=a,
        text=text,
        title=f"{a.name} source",
        meta=f"{text} · x",
        when=NOW - timedelta(days=days),
        url=f"https://{a.domain}/x",
        grade="confirmed",
        lane="salesforce",
        extra={},
    )


def _payload(rid: str = "R1") -> dict[str, Any]:
    return {
        "headline": f"Highmark holds two open Salesforce build roles [{rid}].",
        "stats": [{"label": "open roles", "value": "2"}],
        "sections": [
            {
                "kind": "table",
                "title": "Open roles",
                "columns": ["Role", "Posted", "Source"],
                "rows": [
                    ["Salesforce Architect", "16 Jun 2026", f"[{rid}]"],
                    ["Integration Architect", "2026-06-14", f"[{rid}]"],
                ],
            },
            {
                "kind": "callout",
                "text": f"Both point to one customer data program [{rid}].",
                "tone": "info",
            },
            {"kind": "steps", "title": "Next steps", "items": [f"Call the platform owner [{rid}]"]},
        ],
    }


def _ids() -> tuple[A.Acct, dict[str, A.Row]]:
    a = _acct()
    return a, {"R1": _row(a, "Two open Salesforce build roles point to a customer data program")}


def test_the_shape_check_rejects_what_the_tool_cannot_render() -> None:
    assert RENDER.check_shape(_payload()) is None
    bad = [
        "not a dict",
        {"sections": []},
        {**_payload(), "headline": ""},
        {**_payload(), "surprise": 1},
        {**_payload(), "stats": [{"label": "x"}]},
        {**_payload(), "sections": [{"kind": "chart"}]},
        {**_payload(), "sections": [{"kind": "table", "columns": ["a"], "rows": [["a", "b"]]}]},
        {**_payload(), "sections": [{"kind": "callout", "text": "x", "tone": "angry"}]},
        {**_payload(), "sections": [{"kind": "steps", "items": []}]},
        # prose is sentences; a wall of prose belongs in a table (the parity mandate)
        {**_payload(), "sections": [{"kind": "prose", "paragraphs": ["One. Two. Three. Four."]}]},
        # the owner mandate (2026-10-17): every answer builds a table, narrative ones included
        {**_payload(), "sections": [{"kind": "prose", "paragraphs": ["One."]}]},
        {**_payload(), "sections": [{"kind": "steps", "items": ["Do the thing."]}]},
    ]
    for payload in bad:
        assert RENDER.check_shape(payload) is not None, payload
    no_table = {**_payload(), "sections": [{"kind": "prose", "paragraphs": ["One."]}]}
    assert RENDER.check_shape(no_table)[0] == "no_table"  # type: ignore[index]


def test_rewrite_checks_every_citation_against_the_numbered_rows() -> None:
    _, ids = _ids()
    converted = RENDER.rewrite(_payload(), ids)
    assert converted is not None
    payload, rows = converted
    assert set(rows) == {ids["R1"].key}
    cell = payload["sections"][0]["rows"][0][2]
    assert cell == f"[[{ids['R1'].key}]]"
    # a citation id the model was never sent discards the reply (the local answer stands)
    assert RENDER.rewrite(_payload("R9"), ids) is None
    # stats may be omitted: the stored payload still carries the key (the client's schema)
    no_stats = {**_payload(), "stats": None}
    converted = RENDER.rewrite(no_stats, ids)
    assert converted is not None and converted[0]["stats"] == []


def _workspace_fact() -> tuple[A.Workspace, A.Row]:
    a = _acct()
    fact = _row(a, "Two open Salesforce build roles point to a customer data program")
    return A.Workspace([a], [fact], [], [], [], [], [], {}, False), fact


def _strings(value: Any) -> Any:
    """Every string inside a nested answer structure."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def test_a_rendered_answer_becomes_a_draft_whose_finish_carries_the_structured_payload() -> None:
    ws, fact = _workspace_fact()
    converted = RENDER.rewrite(_payload(), {"R1": fact})
    assert converted is not None
    draft = RENDER.to_draft(*converted)
    draft.focus = fact.account
    q, _ = A.draft_local("Which accounts posted Salesforce roles?", ws, NOW)
    answer = A.finish(draft, q, ws, NOW, None)
    # the structured payload: citations numbered [1], matching the sources list
    structured = answer["structured"]
    assert structured["headline"].endswith("[1].")
    assert structured["sections"][0]["rows"][0][2] == "[1]"
    assert [s["n"] for s in answer["sources"]] == [1]
    assert answer["sources"][0]["title"] == fact.title
    # the same answer as Markdown: a real table, line for line, for the stream and the store
    text = "\n".join(draft.paragraphs)
    assert "| Role | Posted | Source |" in text and "| Salesforce Architect | 16 Jun 2026 |" in text
    assert (
        "> Both point to one customer data program" in text and "1. Call the platform owner" in text
    )
    # the post-processor guarantee: no row marker anywhere in the whole answer
    assert not any("[[" in s for s in _strings(answer))


def test_a_passage_citation_numbers_instead_of_leaking_the_raw_marker() -> None:
    ws, fact = _workspace_fact()
    passage = _row(
        fact.account,
        "…the platform owner owns the roadmap…",
        key=f"passage:{uuid4()}:3",
        kind="passage",
    )
    d = A.Draft()
    d.para(f"The roadmap sits with the platform owner {d.cite(passage)}; see {d.cite(fact)}.")
    d.focus = fact.account
    q, _ = A.draft_local("Who owns the roadmap?", ws, NOW)
    answer = A.finish(d, q, ws, NOW, None)
    parts = [p for part in answer["paragraphs"] for p in part]
    assert [p for p in parts if p["t"] == "cite"] == [
        {"t": "cite", "v": "1"},
        {"t": "cite", "v": "2"},
    ]
    assert [s["kind"] for s in answer["sources"]] == ["passage", "fact"]
    assert not any("[[" in s for s in _strings(answer))
    # the streamed pieces number passage markers the same way finish does
    numbering: dict[str, int] = {}
    piece = A.number_marks(f"The roadmap {d.cite(passage)} then {d.cite(fact)}.", numbering)
    assert [p["v"] for p in piece if p["t"] == "cite"] == ["1", "2"]
    assert not any("[[" in p["v"] for p in piece)


class _RenderProvider:
    """A provider whose model ended its turn with one render_answer call."""

    supports_tools = True

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.kw: dict[str, Any] = {}

    async def stream(self, model: Any, messages: Any, max_tokens: int, **kw: Any) -> Any:
        self.kw = kw
        yield ChatEvent(
            "tool",
            tool={
                "name": "render_answer",
                "input": self.payload,
                "result": {"ok": True, "terminal": True},
            },
        )
        yield ChatEvent("usage", usage=ChatUsage(3, 2))


async def _stream(payload: dict[str, Any]) -> tuple[list[tuple[str, Any]], dict[str, Any]]:
    ws, fact = _workspace_fact()
    q, local = A.draft_local("Which accounts posted Salesforce roles?", ws, NOW)
    block_ids = context_block(pick_rows(q, ws, local))[1]
    rid = next(r for r, row in block_ids.items() if row.key == fact.key)
    provider = _RenderProvider(_payload(rid) if payload is ... else payload)
    events = [
        (k, v)
        async for k, v in stream_with_model(
            provider,
            object(),
            q,
            ws,
            local,
            user_id="u",
            request_id="r",
            tools=[{"name": "render_answer"}],
            run_tool=None,
            temperature=0.0,
        )
    ]
    return events, provider.kw


@pytest.mark.asyncio
async def test_stream_takes_the_answer_from_one_render_call_without_streaming_text() -> None:
    events, kw = await _stream(...)
    kinds = [k for k, _ in events]
    assert kinds == ["tool", "done"]  # the call is the answer: nothing streamed as text
    reply = events[-1][1]
    assert reply.draft.structured is not None
    assert reply.draft.rows, "the cited rows joined the draft"
    # the rules travel only when the tool is on offer, and the tabular rules stay off a
    # turn without the table tools
    assert RENDER.RENDER_RULES in kw["system"] and TABULAR_RULES not in kw["system"]


@pytest.mark.asyncio
async def test_a_render_call_citing_an_unknown_row_resets_to_the_local_answer() -> None:
    payload = _payload("R40")  # never sent to the model
    events, _ = await _stream(payload)
    assert [k for k, _ in events] == ["tool", "reset"]


class _Round:
    """One scripted Anthropic round (streams nothing, then the final message)."""

    def __init__(self, final: Any) -> None:
        self.final = final

    async def __aenter__(self) -> _Round:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    def __aiter__(self) -> Any:
        async def empty() -> Any:
            return
            yield

        return empty()

    async def get_final_message(self) -> Any:
        return self.final


@pytest.mark.asyncio
async def test_a_terminal_tool_result_ends_the_providers_rounds() -> None:
    """render_answer's ok result is terminal: the provider runs the call, yields it and stops —
    no round follows the call that carries the answer."""

    class ToolUse:
        id, name, input, type = "t1", "render_answer", _payload(), "tool_use"

    class Usage:
        input_tokens, output_tokens = 3, 2
        cache_read_input_tokens = cache_creation_input_tokens = 0

    class Final:
        content, stop_reason, usage = [ToolUse()], "tool_use", Usage()

    class Client:
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []
            self.messages = self

        def stream(self, **request: Any) -> _Round:
            self.requests.append(request)
            if len(self.requests) > 1:
                raise AssertionError("a round followed the terminal call")
            return _Round(Final())

    client = Client()

    async def run_tool(name: str, payload: dict[str, Any]) -> Any:
        assert name == "render_answer"
        return {"ok": True, "terminal": True}

    events = [
        e
        async for e in AnthropicChatProvider(client).stream(
            type("M", (), {"id": "model-z"})(),
            [ChatMessage("user", "q")],
            500,
            system="rules",
            tools=[RENDER.RENDER_TOOL],
            run_tool=run_tool,
        )
    ]
    assert [e.kind for e in events] == ["tool", "usage"]
    assert events[0].tool["input"]["headline"].startswith("Highmark holds")
    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_the_access_tools_shape_check_the_render_call_and_make_it_terminal() -> None:
    tools = AccessTools(None, [], [])  # type: ignore[arg-type]
    good = await tools.run("render_answer", _payload())
    assert good == {"ok": True, "terminal": True}
    assert tools.rendered == _payload()
    bad = await tools.run("render_answer", {"sections": []})
    assert bad["error"]["code"] == "bad_headline"
    assert "render_answer" in [t["name"] for t in tools.offered("prose")]
    assert "render_answer" in [t["name"] for t in tools.offered("tabular")]
    assert "render_answer" in [t["name"] for t in tools.offered("mixed")]


def test_ensure_table_builds_a_facts_table_when_the_answer_has_none() -> None:
    a = _acct()
    row = _row(a, "Hiring a Salesforce admin | remote", key="fact:1")
    d = A.Draft()
    d.para(f"Highmark is hiring {d.cite(row)}.")
    assert not RENDER.has_table(d)
    assert RENDER.ensure_table(d) is True
    assert RENDER.has_table(d)
    table = d.paragraphs[-1]
    assert "| Account | Type | Fact | Grade | Source |" in table
    assert "Hiring a Salesforce admin / remote" in table and "[[fact:1]]" in table
    assert RENDER.ensure_table(d) is False  # already has one: nothing added twice


def test_ensure_table_leaves_a_table_answer_and_an_empty_answer_alone() -> None:
    d = A.Draft()
    d.para("Nothing found.")
    assert RENDER.ensure_table(d) is False and d.paragraphs == ["Nothing found."]
    d.structured = {
        "headline": "h",
        "sections": [{"kind": "table", "columns": ["a"], "rows": [["1"]]}],
    }
    assert RENDER.has_table(d)
