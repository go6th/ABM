"""The Kimi K3 adapter against a mock transport shaped like Moonshot's documented API (no network,
no key): request body, usage, streaming, and the documented error types."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from llm.adapters.moonshot_api import MoonshotClient, map_status, request_body, usage_of
from llm.errors import (
    AuthFailed,
    ContentRefused,
    ContextTooLong,
    ModelUnavailable,
    Overloaded,
    ProviderCreditsExhausted,
)

pytestmark = pytest.mark.asyncio


def _client(handler: Any) -> MoonshotClient:
    return MoonshotClient(
        "test-key", http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def test_moonshot_requests_and_errors_follow_the_documentation() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "model": "kimi-k3",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": '{"ok": true}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 30, "cache_write_tokens": 10},
                },
            },
        )

    client = _client(handler)
    result = await client.complete(
        "kimi-k3", "rules", "rows", max_tokens=4000, effort="medium",
        json_schema={"type": "object"},
    )  # fmt: skip
    assert result.text == '{"ok": true}' and result.model == "kimi-k3"
    assert result.usage.input_tokens == 60 and result.usage.output_tokens == 20
    assert result.usage.cache_read_input_tokens == 30
    assert result.usage.cache_creation_input_tokens == 10
    request = seen[0]
    assert str(request.url) == "https://api.moonshot.ai/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["max_completion_tokens"] == 4000 and "max_tokens" not in body
    assert body["reasoning_effort"] == "high" and "thinking" not in body
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


async def test_the_key_never_appears_in_an_error() -> None:
    client = _client(
        lambda r: httpx.Response(
            401,
            json={"error": {"type": "invalid_authentication_error", "message": "bad key test-key"}},
        )
    )
    with pytest.raises(AuthFailed) as raised:
        await client.complete("kimi-k3", "s", "u", max_tokens=10)
    assert "test-key" not in str(raised.value)


@pytest.mark.parametrize(
    ("status", "kind", "message", "expected"),
    [
        (429, "rate_limit_reached_error", "", Overloaded),
        (429, "engine_overloaded_error", "", Overloaded),
        (429, "exceeded_current_quota_error", "", ProviderCreditsExhausted),
        (500, "server_error", "", Overloaded),
        (503, "server_unavailable", "", Overloaded),
        (504, "", "", Overloaded),
        (401, "incorrect_api_key_error", "", AuthFailed),
        (403, "permission_denied_error", "", AuthFailed),
        (404, "resource_not_found_error", "", ModelUnavailable),
        (400, "content_filter", "", ContentRefused),
        (400, "invalid_request_error", "exceeds the context window", ContextTooLong),
    ],
)
async def test_every_documented_error_type_maps(
    status: int, kind: str, message: str, expected: type[Exception]
) -> None:
    assert isinstance(map_status(status, {"error": {"type": kind, "message": message}}), expected)


async def test_a_stream_yields_text_then_one_usage() -> None:
    chunks = [
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo."}}]},
        {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}},
    ]
    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
        return httpx.Response(
            200, content=sse.encode(), headers={"content-type": "text/event-stream"}
        )

    events = [e async for e in _client(handler).stream(
        "kimi-k3", "rules", [{"role": "user", "content": "q"}], max_tokens=50)]  # fmt: skip
    assert events[:2] == [("text", "Hel"), ("text", "lo.")]
    assert events[-1][0] == "usage" and events[-1][1].output_tokens == 2


async def test_kimi_streams_tool_calls_and_their_results_go_back() -> None:
    """The Ask tool harness on Kimi K3: tools go out in the OpenAI functions shape with the
    sampling pinned; streamed ``tool_calls`` fragments assemble by index; the round's calls and
    their results ride the transcript verbatim in the next round."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        chunks = [
            {"choices": [{"delta": {"content": "Checking."}}]},
            # one call, split over two chunks, out of order with a second call whole
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 1,
                                    "id": "b1",
                                    "function": {
                                        "name": "describe_table",
                                        "arguments": '{"table": "Jobs"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "a1",
                                    "function": {"name": "query_table", "arguments": '{"tab'},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [{"index": 0, "function": {"arguments": 'le": "Jobs"}'}}]
                        }
                    }
                ]
            },
            {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 4}},
        ]
        sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=sse.encode())

    tools = [{"name": "query_table", "description": "d", "input_schema": {"type": "object"}}]
    events = [e async for e in _client(handler).stream(
        "kimi-k3", "rules",
        [{"role": "user", "content": "q"},
         {"role": "assistant", "content": "", "tool_calls": [{"id": "x", "type": "function",
            "function": {"name": "describe_table", "arguments": "{}"}}]},
         {"role": "tool", "tool_call_id": "x", "content": "{}"}],
        max_tokens=500, tools=tools, temperature=0.0)]  # fmt: skip

    body = seen[0]
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "query_table",
                "description": "d",
                "parameters": {"type": "object"},
            },
        }
    ]
    assert body["tool_choice"] == "auto" and body["temperature"] == 0.0
    # an assistant round with tool calls keeps them verbatim; a tool result is tool_call_id-scoped
    assert body["messages"][2]["tool_calls"][0]["id"] == "x"
    assert body["messages"][3]["role"] == "tool" and body["messages"][3]["tool_call_id"] == "x"
    kinds = [k for k, _ in events]
    assert kinds == ["text", "tools", "usage"]
    text, calls, usage = events[0][1], events[1][1], events[2][1]
    assert text == "Checking."
    assert [c.name for c in calls] == ["query_table", "describe_table"]  # index order
    assert calls[0].id == "a1" and calls[0].input == {"table": "Jobs"}
    assert calls[0].raw_arguments == '{"table": "Jobs"}'
    assert calls[1].input == {"table": "Jobs"}
    assert usage.output_tokens == 4


async def test_the_body_never_sends_empty_content() -> None:
    body = request_body("kimi-k3", "", "", 10)
    assert all(m["content"] for m in body["messages"])
    assert usage_of(None).input_tokens == 0


async def test_ollama_cloud_gets_its_model_tag_and_token_field() -> None:
    """Ollama Cloud (owner decision 2026-10-15): the same API at ollama.com/v1, its own model tag,
    and ``max_tokens``; the result still names the catalogue id, so prices and records stay one
    model. A stream sends the same tag and field."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://ollama.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        seen.append(body)
        if body.get("stream"):
            sse = (
                'data: {"choices": [{"delta": {"content": "ok"}}]}\n\n'
                'data: {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, content=sse.encode())
        return httpx.Response(
            200,
            json={
                "model": "kimi-k3:cloud",
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    client = MoonshotClient(
        "test-key",
        base_url="https://ollama.com/v1",
        dialect="ollama",
        remote_model="kimi-k3:cloud",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await client.complete("kimi-k3", "rules", "q", max_tokens=100, effort="high")
    assert seen[0]["model"] == "kimi-k3:cloud" and seen[0]["max_tokens"] == 100
    assert "max_completion_tokens" not in seen[0]
    assert result.model == "kimi-k3" and result.extra["remote_model"] == "kimi-k3:cloud"
    assert result.extra["provider"] == "ollama"
    events = [e async for e in client.stream(
        "kimi-k3", "rules", [{"role": "user", "content": "q"}], max_tokens=40)]  # fmt: skip
    assert seen[1]["model"] == "kimi-k3:cloud" and seen[1]["max_tokens"] == 40
    assert events[0] == ("text", "ok") and events[-1][1].output_tokens == 1
    with pytest.raises(ValueError):
        MoonshotClient("test-key", dialect="openrouter")
