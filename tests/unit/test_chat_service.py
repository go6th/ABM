"""Message validation, conservative spend estimates and server-side Anthropic streaming."""

from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from account_intelligence.chat.registry import ChatConfigInvalid, ChatModel, get_chat_registry
from account_intelligence.chat.service import (
    GROUNDING_MAX_CHARS,
    AnthropicChatProvider,
    ChatEvent,
    ChatMessage,
    ChatService,
    ChatUsage,
    actual_cost,
    estimated_tokens,
    sse,
)
from account_intelligence.config import CONFIG_DIR


def test_message_and_context_limits_fail_before_provider_calls() -> None:
    registry = get_chat_registry()
    service = ChatService(registry, None)
    model = registry.model(None)
    assert service.validate_messages([], "hello", model) == [ChatMessage("user", "hello")]
    with pytest.raises(ValueError, match="empty"):
        service.validate_messages([], "  ", model)
    with pytest.raises(ValueError, match="empty"):
        service.validate_messages([], "\x00", model)
    with pytest.raises(ValueError, match="inconsistent"):
        service.validate_messages([ChatMessage("assistant", "out of order")], "next", model)
    with pytest.raises(ValueError, match="inconsistent"):
        service.validate_messages([ChatMessage("user", "")], "next", model)
    too_long = "x" * (registry.limits.max_input_chars + 1)
    with pytest.raises(ValueError, match="input limit"):
        service.validate_messages([], too_long, model)

    tiny = ChatModel(
        **{
            **model.__dict__,
            "context_window": 1030,
            "max_output_tokens": 10,
        }
    )
    with pytest.raises(ValueError, match="context window"):
        service.validate_messages([], "x", tiny)


def test_estimates_and_actual_cost_include_cache_usage() -> None:
    model = get_chat_registry().model(None)
    raw = next(
        m
        for m in yaml.safe_load((CONFIG_DIR / "chat_models.yaml").read_text())["models"]
        if m["id"] == model.id
    )
    expected_price = (
        100 * Decimal(raw["input_usd_per_million"])
        + 20 * Decimal(raw["output_usd_per_million"])
        + 30 * Decimal(raw["cache_read_usd_per_million"])
        + 10 * Decimal(raw["cache_write_usd_per_million"])
    ) / Decimal(1_000_000)
    messages = [ChatMessage("user", "hello"), ChatMessage("assistant", "world")]
    assert estimated_tokens(messages) >= len("helloworld") + 1024
    price = actual_cost(
        model,
        ChatUsage(input_tokens=100, output_tokens=20, cache_read_tokens=30, cache_write_tokens=10),
    )
    assert price == expected_price
    registry = get_chat_registry()
    service = ChatService(registry, None)
    messages = [ChatMessage("user", "prompt")]
    estimate = estimated_tokens(messages) + GROUNDING_MAX_CHARS + 2_048
    reserved = service.reserve_cost(model, messages)
    expected = (
        Decimal(estimate) * model.cache_write_usd_per_million
        + Decimal(registry.limits.max_output_tokens) * model.output_usd_per_million
    ) / Decimal(1_000_000)
    assert reserved == expected
    with pytest.raises(ValueError, match="invalid usage"):
        actual_cost(model, ChatUsage(input_tokens=-1, output_tokens=0))
    with pytest.raises(ChatConfigInvalid):
        get_chat_registry().model("unknown")
    record = sse("text", {"text": "snowman ☃"})
    assert record.startswith(b"event: text\ndata: ") and "☃".encode() in record


@pytest.mark.asyncio
async def test_anthropic_adapter_streams_only_text_and_final_usage() -> None:
    calls: list[dict[str, Any]] = []

    class ProviderStream:
        text_stream = iter(())

        async def __aenter__(self) -> ProviderStream:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get_final_message(self) -> Any:
            return SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=3,
                    output_tokens=2,
                    cache_read_input_tokens=1,
                    cache_creation_input_tokens=0,
                )
            )

    class TextStream:
        def __aiter__(self):
            async def chunks():
                yield "Hello"
                yield " world"

            return chunks()

    @asynccontextmanager
    async def stream(**kwargs: Any):
        calls.append(kwargs)
        current = ProviderStream()
        current.text_stream = TextStream()
        yield current

    client = SimpleNamespace(messages=SimpleNamespace(stream=stream))
    provider = AnthropicChatProvider(client)
    model = get_chat_registry().model(None)
    events = [
        event async for event in provider.stream(model, [ChatMessage("user", "question")], 1024)
    ]
    assert [event.text for event in events if event.kind == "text"] == ["Hello", " world"]
    assert events[-1] == ChatEvent("usage", usage=ChatUsage(3, 2, 1, 0))
    assert calls[0]["model"] == model.id and calls[0]["max_tokens"] == 1024
    assert calls[0]["messages"] == [{"role": "user", "content": "question"}]
    assert "temperature" not in calls[0]
    assert "Answer ONLY from the WORKSPACE DATA" in calls[0]["system"]
