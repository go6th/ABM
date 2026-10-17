"""The Anthropic provider's tool loop (tabular questions, 2026-10-17) against a fake SDK client:
a round that ends in a tool call runs the tool, sends the result back and streams the next
round; text written before a tool call is withdrawn; usage sums every round; the loop is
bounded; ``stream_with_model`` passes the tools, the rules and the temperature through.

GUARANTEE(test_the_provider_runs_tool_calls_and_streams_the_final_round)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.chat.service import (
    MAX_TOOL_ROUNDS,
    AnthropicChatProvider,
    ChatMessage,
)
from account_intelligence.replica import answers as A
from account_intelligence.replica.model_answer import TABULAR_RULES, stream_with_model
from account_intelligence.replica.tools import AccessTools


@dataclass
class _Text:
    text: str
    type: str = "text"


@dataclass
class _ToolUse:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class _Usage:
    input_tokens: int = 10
    output_tokens: int = 5
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _Final:
    content: list[Any]
    stop_reason: str
    usage: _Usage = field(default_factory=_Usage)


class _Stream:
    """One round: streams the text blocks' text, then the final message."""

    def __init__(self, final: _Final) -> None:
        self.final = final

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    def __aiter__(self) -> Any:
        async def events() -> Any:
            for block in self.final.content:
                if block.type == "text":
                    for piece in (
                        block.text[: len(block.text) // 2],
                        block.text[len(block.text) // 2 :],
                    ):
                        if piece:
                            yield _Text(piece)

        return events()

    async def get_final_message(self) -> _Final:
        return self.final


class _Client:
    """A scripted SDK client: one final message per round, every request recorded."""

    def __init__(self, rounds: list[_Final]) -> None:
        self.rounds = list(rounds)
        self.requests: list[dict[str, Any]] = []
        self.messages = self

    def stream(self, **request: Any) -> _Stream:
        self.requests.append(json.loads(json.dumps(request, default=str)))
        if not self.rounds:
            raise AssertionError("unscripted round")
        return _Stream(self.rounds.pop(0))


def _model() -> Any:
    return type("M", (), {"id": "model-z"})()


@pytest.mark.asyncio
async def test_the_provider_runs_tool_calls_and_streams_the_final_round() -> None:
    client = _Client(
        [
            _Final(
                [_Text("Let me check."), _ToolUse("t1", "describe_table", {"table": "Jobs"})],
                "tool_use",
            ),
            _Final([_ToolUse("t2", "query_table", {"table": "Jobs", "where": []})], "tool_use"),
            _Final([_Text("No open postings [R1].")], "end_turn", _Usage(20, 7, 3, 0)),
        ]
    )
    ran: list[tuple[str, dict[str, Any]]] = []

    async def run_tool(name: str, payload: dict[str, Any]) -> Any:
        ran.append((name, payload))
        return {"ok": name}

    tools = [{"name": "describe_table", "input_schema": {"type": "object"}}]
    events = []
    async for e in AnthropicChatProvider(client).stream(
        _model(),
        [ChatMessage("user", "q")],
        500,
        system="rules",
        tools=tools,
        run_tool=run_tool,
        temperature=0.0,
    ):
        events.append(e)
    kinds = [e.kind for e in events]
    # the first round's text streamed, then was withdrawn; the answer is the last round's text
    assert kinds == ["text", "text", "discard", "tool", "tool", "text", "text", "usage"]
    assert "".join(e.text for e in events[:2]) == "Let me check."
    assert "".join(e.text for e in events[5:7]) == "No open postings [R1]."
    assert ran == [
        ("describe_table", {"table": "Jobs"}),
        ("query_table", {"table": "Jobs", "where": []}),
    ]
    assert events[3].tool == {
        "name": "describe_table",
        "input": {"table": "Jobs"},
        "result": {"ok": "describe_table"},
    }
    usage = events[-1].usage
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens) == (40, 17, 3)
    # every request carried the tools; a caller's temperature is never forwarded — the
    # installed SDK's Messages call has no temperature parameter (OI-LLM-1) and the tabular
    # turn's 0.0 would TypeError every round
    assert all(r["tools"] == tools and "temperature" not in r for r in client.requests)
    second = client.requests[1]["messages"]
    assert second[1] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "t1", "name": "describe_table", "input": {"table": "Jobs"}},
        ],
    }
    assert second[2]["role"] == "user" and second[2]["content"][0]["tool_use_id"] == "t1"
    assert json.loads(second[2]["content"][0]["content"]) == {"ok": "describe_table"}
    assert len(client.requests[2]["messages"]) == 5


@pytest.mark.asyncio
async def test_the_tool_loop_is_bounded_and_text_only_turns_send_no_tools() -> None:
    endless = [
        _Final([_ToolUse(f"t{i}", "query_table", {})], "tool_use")
        for i in range(MAX_TOOL_ROUNDS + 3)
    ]
    client = _Client(endless)

    async def run_tool(name: str, payload: dict[str, Any]) -> Any:
        return {}

    events = [
        e
        async for e in AnthropicChatProvider(client).stream(
            _model(), [ChatMessage("user", "q")], 100, tools=[{"name": "x"}], run_tool=run_tool
        )
    ]
    assert sum(1 for e in events if e.kind == "tool") == MAX_TOOL_ROUNDS
    assert events[-1].kind == "usage" and len(client.requests) == MAX_TOOL_ROUNDS + 1
    plain = _Client([_Final([_Text("Hi [R1].")], "end_turn")])
    events = [
        e
        async for e in AnthropicChatProvider(plain).stream(
            _model(), [ChatMessage("user", "q")], 100
        )
    ]
    assert [e.kind for e in events] == ["text", "text", "usage"]
    assert "tools" not in plain.requests[0] and "temperature" not in plain.requests[0]


class _MoonshotClient:
    """A scripted Moonshot client: one scripted round per call, messages recorded."""

    def __init__(self, rounds: list[list[tuple[str, Any]]]) -> None:
        self.rounds = [list(r) for r in rounds]
        self.calls: list[dict[str, Any]] = []

    async def stream(self, model_id: str, system: str, messages: Any, **kw: Any) -> Any:
        self.calls.append({"model": model_id, "system": system, "messages": messages, **kw})
        assert self.rounds, "unscripted round"
        for event in self.rounds.pop(0):
            yield event


@pytest.mark.asyncio
async def test_kimi_runs_the_tool_loop_like_claude() -> None:
    """The answering model is Kimi K3 (owner decision 2026-10-14): the tool loop is the same
    there — text before a call is withdrawn, arguments broken JSON is answered with a
    structured error, and the transcript carries the round and its results verbatim."""
    from account_intelligence.chat.service import MoonshotChatProvider
    from account_intelligence.platform.ports import LlmUsage
    from llm.adapters.moonshot_api import ToolCall

    client = _MoonshotClient(
        [
            [
                ("text", "Let me check."),
                (
                    "tools",
                    [
                        ToolCall("c1", "describe_table", {"table": "Jobs"}, '{"table": "Jobs"}'),
                        ToolCall("c2", "query_table", None, "not-json"),
                    ],
                ),
                ("usage", LlmUsage(10, 5)),
            ],
            [("text", "No open postings [R1]."), ("usage", LlmUsage(20, 7, 3, 0))],
        ]
    )

    async def run_tool(name: str, payload: dict[str, Any]) -> Any:
        return {"ok": name}

    tools = [{"name": "describe_table", "input_schema": {"type": "object"}}]
    events = [
        e
        async for e in MoonshotChatProvider(client).stream(
            _model(),
            [ChatMessage("user", "q")],
            500,
            system="rules",
            tools=tools,
            run_tool=run_tool,
            temperature=0.0,
        )
    ]
    assert [e.kind for e in events] == ["text", "discard", "tool", "tool", "text", "usage"]
    # the broken call never ran: it got the structured error instead
    assert events[2].tool == {
        "name": "describe_table",
        "input": {"table": "Jobs"},
        "result": {"ok": "describe_table"},
    }
    assert events[3].tool["result"]["error"]["code"] == "bad_arguments"
    usage = events[-1].usage
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens) == (30, 12, 3)
    sent = client.calls
    assert all(c["tools"] == tools and c["temperature"] == 0.0 for c in sent)
    second = sent[1]["messages"]
    assert second[1]["role"] == "assistant" and second[1]["tool_calls"][0]["id"] == "c1"
    assert second[2] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": json.dumps({"ok": "describe_table"}),
    }
    assert second[3]["role"] == "tool" and second[3]["tool_call_id"] == "c2"


class _Recorder:
    """A provider that records what ``stream_with_model`` asks of it."""

    supports_tools = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def stream(self, model: Any, messages: Any, max_tokens: int, **kw: Any) -> Any:
        self.calls.append(kw)
        yield type("E", (), {"kind": "discard", "text": "", "usage": None, "tool": None})()
        yield type(
            "E", (), {"kind": "tool", "text": "", "usage": None, "tool": {"name": "query_table"}}
        )()
        yield type(
            "E",
            (),
            {"kind": "text", "text": "Zero open postings [R1].", "usage": None, "tool": None},
        )()
        yield type("E", (), {"kind": "usage", "text": "", "usage": None, "tool": None})()


@pytest.mark.asyncio
async def test_stream_with_model_offers_the_tools_the_rules_and_the_temperature() -> None:
    acct = A.Acct(uuid4(), "gsk.example", "GSK", [], None, None, 1)
    ws = A.Workspace([acct], [], [], [], [], [], [], {}, False)
    ws.tables = [
        A.Row(
            "table:1",
            "file",
            acct,
            "GSK.xlsx, table Jobs: 644 rows",
            "From your files: GSK.xlsx (table Jobs)",
            "Internal",
            None,
            None,
            "internal",
            extra={"table": "Jobs"},
        )
    ]
    q, local = A.draft_local("List all open Salesforce jobs", ws, datetime.now(UTC))
    provider = _Recorder()

    async def run_tool(name: str, payload: dict[str, Any]) -> Any:
        return {}

    out = [
        (k, v)
        async for k, v in stream_with_model(
            provider,
            _model(),
            q,
            ws,
            local,
            user_id="u",
            request_id="r",
            tools=[{"name": "query_table"}],
            run_tool=run_tool,
            temperature=0.0,
        )
    ]
    assert [k for k, _ in out] == ["discard", "tool", "piece", "done"]
    kw = provider.calls[0]
    assert (
        kw["tools"] == [{"name": "query_table"}]
        and kw["run_tool"] is run_tool
        and kw["temperature"] == 0.0
    )
    assert TABULAR_RULES in kw["system"] and "describe_table" in TABULAR_RULES
    assert out[-1][1].draft.paragraphs == ["Zero open postings [[table:1]]."]
    # without tools the rules stay out of the prompt
    plain = _Recorder()
    [x async for x in stream_with_model(plain, _model(), q, ws, local, user_id="u", request_id="r")]
    assert TABULAR_RULES not in plain.calls[0]["system"] and plain.calls[0]["tools"] is None


@pytest.mark.asyncio
async def test_run_python_joins_the_offer_only_for_a_flagged_tenant() -> None:
    # exposure by route plus the run_python flag (the brief's Part E): never on the prose
    # route, on tabular and mixed only when the tenant's flag is on; refused closed otherwise;
    # the turn's results so far are the code's data and every run is counted
    off = AccessTools(lambda: None, [], [])  # type: ignore[arg-type]
    assert [t["name"] for t in off.offered("tabular")] == [
        "list_sources",
        "describe_table",
        "query_table",
        "read_rows",
        "render_answer",
    ]
    assert [t["name"] for t in off.offered("prose")] == [
        "list_sources",
        "search_passages",
        "read_passage",
        "read_span",
        "render_answer",
    ]
    on = AccessTools(lambda: None, [], [], run_python=True)  # type: ignore[arg-type]
    assert "run_python" not in [t["name"] for t in on.offered("prose")]
    assert [t["name"] for t in on.offered("mixed")] == [
        "list_sources",
        "describe_table",
        "query_table",
        "read_rows",
        "search_passages",
        "read_passage",
        "read_span",
        "render_answer",
        "run_python",
    ]
    refused = await off.run("run_python", {"code": "result = 1"})
    assert refused["error"]["code"] == "disabled" and off.stats()["run_python_used"] == 0
    first = await on.run("run_python", {"code": "result = len(data)"})
    assert first["result"] == 0 and len(first["code_sha256"]) == 64
    second = await on.run("run_python", {"code": "result = data[0]['result'] + 1"})
    assert second["result"] == 1  # the first run's result was the second run's data
    assert on.stats()["run_python_used"] == 2
