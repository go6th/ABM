"""The Grok 4.5 adapter against a mock transport shaped like xAI's documented API (no network,
no key): request body, usage (reasoning billed as output), streaming, tool calls and the error
types."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from llm.adapters.xai_api import XaiClient, map_status, request_body, usage_of
from llm.choice import catalogue
from llm.errors import (
    AuthFailed,
    ContextTooLong,
    ModelUnavailable,
    Overloaded,
    ProviderCreditsExhausted,
)

pytestmark = pytest.mark.asyncio
# the model id comes from config, never a literal (the R1 static guard)
GROK = next(m.id for m in catalogue().models if m.provider == "xai")


def _client(handler: Any) -> XaiClient:
    return XaiClient("test-key", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_xai_requests_and_errors_follow_the_documentation() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "model": f"{GROK}-latest",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": '{"ok": true}'},
                        "finish_reason": "stop",
                    }
                ],
                # the documented example's shape: reasoning apart from completion_tokens
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 230,
                    "prompt_tokens_details": {"cached_tokens": 30},
                    "completion_tokens_details": {"reasoning_tokens": 110},
                },
            },
        )

    client = _client(handler)
    result = await client.complete(
        GROK, "rules", "rows", max_tokens=4000, effort="medium", json_schema={"type": "object"}
    )
    assert result.text == '{"ok": true}' and result.model == GROK
    assert result.extra["provider"] == "xai" and result.extra["remote_model"] == f"{GROK}-latest"
    assert result.usage.input_tokens == 70 and result.usage.cache_read_input_tokens == 30
    assert result.usage.output_tokens == 130  # reasoning is billed at the output price
    assert result.usage.cache_creation_input_tokens == 0  # xAI bills no cache write
    request = seen[0]
    assert str(request.url) == "https://api.x.ai/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["model"] == GROK
    assert body["max_tokens"] == 4000 and "max_completion_tokens" not in body
    assert body["reasoning_effort"] == "medium"
    assert body["response_format"]["json_schema"]["strict"] is True
    # refused by reasoning models (guides/reasoning), so never sent
    assert not {"stop", "presence_penalty", "frequency_penalty"} & set(body)
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


@pytest.mark.parametrize(
    ("effort", "sent"),
    [("low", "low"), ("medium", "medium"), ("high", "high"), ("xhigh", "high"), ("max", "high")],
)
async def test_every_effort_maps_onto_groks_three(effort: str, sent: str) -> None:
    assert request_body(GROK, "s", "u", 10, effort=effort)["reasoning_effort"] == sent
    assert "reasoning_effort" not in request_body(GROK, "s", "u", 10)


async def test_the_key_never_appears_in_an_error() -> None:
    client = _client(
        lambda r: httpx.Response(401, json={"code": "unauthenticated", "error": "bad test-key"})
    )
    with pytest.raises(AuthFailed) as raised:
        await client.complete(GROK, "s", "u", max_tokens=10)
    assert "test-key" not in str(raised.value)


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (429, {"code": "rate_limited", "error": "too many requests"}, Overloaded),
        (
            429,
            {"code": "x", "error": "Your team has used all available credits"},
            ProviderCreditsExhausted,
        ),
        (403, {"code": "x", "error": "monthly spending limit reached"}, ProviderCreditsExhausted),
        (500, {}, Overloaded),
        (502, {}, Overloaded),
        (503, {}, Overloaded),
        (401, {"error": "no authorization header"}, AuthFailed),
        (403, {"error": "forbidden"}, AuthFailed),
        (404, {"error": "model not found"}, ModelUnavailable),
        (400, {"error": "This model's maximum prompt length is 500000"}, ContextTooLong),
        (
            400,
            {"error": {"type": "invalid_request", "message": "context too long"}},
            ContextTooLong,
        ),
    ],
)
async def test_every_documented_error_maps(
    status: int, body: dict[str, Any], expected: type[Exception]
) -> None:
    assert isinstance(map_status(status, body), expected)


async def test_a_stream_yields_text_then_the_last_running_usage() -> None:
    """xAI puts the running usage on every chunk; the last one is the total."""
    chunks = [
        {"choices": [{"delta": {"content": "Hel"}}], "usage": {"prompt_tokens": 9}},
        {
            "choices": [{"delta": {"content": "lo."}}],
            "usage": {
                "prompt_tokens": 9,
                "completion_tokens": 2,
                "completion_tokens_details": {"reasoning_tokens": 5},
            },
        },
    ]
    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True and "stream_options" not in body
        assert body["reasoning_effort"] == "high"
        return httpx.Response(
            200, content=sse.encode(), headers={"content-type": "text/event-stream"}
        )

    events = [e async for e in _client(handler).stream(
        GROK, "rules", [{"role": "user", "content": "q"}], max_tokens=50,
        effort="high")]  # fmt: skip
    assert events[:2] == [("text", "Hel"), ("text", "lo.")]
    assert events[-1][0] == "usage" and events[-1][1].output_tokens == 7


async def test_grok_streams_tool_calls_and_their_results_go_back() -> None:
    """The Ask tool harness on Grok 4.5: tools go out in the OpenAI functions shape; streamed
    ``tool_calls`` fragments assemble by index; the earlier round's calls and their results ride
    the transcript verbatim."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        chunks = [
            {"choices": [{"delta": {"content": "Checking."}}]},
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {"name": "query_table", "arguments": '{"tab'},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'le": 1}'}}]}}
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 4},
            },
        ]
        sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=sse.encode())

    tools = [{"name": "query_table", "description": "d", "input_schema": {"type": "object"}}]
    events = [e async for e in _client(handler).stream(
        GROK, "rules",
        [{"role": "user", "content": "q"},
         {"role": "assistant", "content": "", "tool_calls": [{"id": "x", "type": "function",
            "function": {"name": "describe_table", "arguments": "{}"}}]},
         {"role": "tool", "tool_call_id": "x", "content": "{}"}],
        max_tokens=500, tools=tools, temperature=0.0)]  # fmt: skip

    body = seen[0]
    assert body["tools"][0]["function"]["parameters"] == {"type": "object"}
    assert body["tool_choice"] == "auto" and body["temperature"] == 0.0 and body["seed"] == 0
    assert body["messages"][2]["tool_calls"][0]["id"] == "x"
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "x", "content": "{}"}
    assert [k for k, _ in events] == ["text", "tools", "usage"]
    calls = events[1][1]
    assert [(c.id, c.name, c.input) for c in calls] == [("call_1", "query_table", {"table": 1})]


async def test_a_reply_cut_off_at_its_limit_reads_as_max_tokens() -> None:
    """``length`` is the schema re-ask's ``max_tokens`` (it doubles the limit and asks again)."""
    client = _client(
        lambda r: httpx.Response(
            200, json={"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]}
        )
    )
    result = await client.complete(GROK, "s", "u", max_tokens=10)
    assert result.extra["stop_reason"] == "max_tokens"


async def test_connection_failures_are_retryable_and_the_key_is_required() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(Overloaded):
        await _client(handler).complete(GROK, "s", "u", max_tokens=10)
    with pytest.raises(ValueError):
        XaiClient("")
    assert usage_of(None).input_tokens == 0


async def test_the_chat_provider_runs_the_tool_loop_on_grok() -> None:
    """Ask on Grok 4.5: ``XaiChatProvider`` streams the answer, runs the round's tool calls and
    sends their results back, with the user's chosen effort."""
    from account_intelligence.chat.registry import get_chat_registry
    from account_intelligence.chat.service import ChatMessage, XaiChatProvider

    rounds: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        rounds.append(body)
        if len(rounds) == 1:
            chunks: list[dict[str, Any]] = [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c1",
                                        "function": {"name": "query_table", "arguments": "{}"},
                                    }
                                ]
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 1},
                }
            ]
        else:
            chunks = [
                {
                    "choices": [{"delta": {"content": "Two rows."}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 3},
                }
            ]
        sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=sse.encode())

    ran: list[str] = []

    async def run_tool(name: str, arguments: dict[str, Any]) -> Any:
        ran.append(name)
        return {"rows": 2}

    model = get_chat_registry().models[GROK]
    provider = XaiChatProvider(_client(handler), effort="medium")
    tools = [{"name": "query_table", "description": "d", "input_schema": {"type": "object"}}]
    events = [e async for e in provider.stream(
        model, [ChatMessage("user", "how many?")], 500, system="rules", tools=tools,
        run_tool=run_tool, effort="high")]  # fmt: skip
    assert ran == ["query_table"]
    assert [e.kind for e in events] == ["tool", "text", "usage"]
    assert rounds[0]["reasoning_effort"] == "high" and rounds[1]["messages"][-1]["role"] == "tool"
    usage = events[-1].usage
    assert usage is not None and (usage.input_tokens, usage.output_tokens) == (30, 4)
