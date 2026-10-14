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
