"""A reply cut off at its token limit (staging 2026-11-06, Olympus on Haiku 5.5: the sense check
and distill spent their limits thinking, the Summary had no narrative): the re-ask goes to the
model's own output limit, and a call above the SDK's non-streamed ceiling is streamed."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel

from account_intelligence.platform.llm import AnthropicLlm
from account_intelligence.platform.ports import LlmResult, LlmUsage
from llm.route import MAX_REASK_TOKENS, STREAM_ABOVE_TOKENS, require

pytestmark = pytest.mark.asyncio


def _message() -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
        usage=SimpleNamespace(
            input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        model="m",
        stop_reason="end_turn",
    )  # fmt: skip


class _Stream:
    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get_final_message(self) -> Any:
        return _message()


class _Messages:
    def __init__(self) -> None:
        self.used: list[tuple[str, int]] = []

    async def create(self, **kw: Any) -> Any:
        self.used.append(("create", kw["max_tokens"]))
        return _message()

    def stream(self, **kw: Any) -> _Stream:
        self.used.append(("stream", kw["max_tokens"]))
        return _Stream()


async def test_a_limit_above_the_non_streamed_ceiling_is_streamed() -> None:
    messages = _Messages()
    port = AnthropicLlm(SimpleNamespace(messages=messages), {}, "anthropic")
    await port._send_once({"max_tokens": STREAM_ABOVE_TOKENS}, None)
    await port._send_once({"max_tokens": MAX_REASK_TOKENS}, None)
    assert messages.used == [("create", STREAM_ABOVE_TOKENS), ("stream", MAX_REASK_TOKENS)]


async def test_a_cut_off_reply_is_asked_again_at_the_models_own_limit() -> None:
    from agents.production.wiring import RunDepsModel

    class Out(BaseModel):
        value: int

    limits: list[int] = []

    class Deps:
        tenant_id, run_id = uuid4(), uuid4()
        models = None

        def chosen_model(self, tier: str, override: str | None = None) -> str | None:
            return None

        async def model(self, task_kind: str, system: str, user: str, **kw: Any) -> LlmResult:
            limits.append(kw["max_tokens"])
            if len(limits) == 1:  # spent its limit thinking: no JSON
                return LlmResult('{"val', "m", LlmUsage(1, 1), {"stop_reason": "max_tokens"})
            return LlmResult('{"value": 1}', "m", LlmUsage(1, 1))

    out, _ = await RunDepsModel(Deps()).call(  # type: ignore[arg-type]
        "abm_distill",
        "prompt",
        "h",
        {},
        Out,
        None,  # type: ignore[arg-type]
    )
    assert out.value == 1
    assert limits == [require("abm_distill").max_tokens, MAX_REASK_TOKENS]
    assert MAX_REASK_TOKENS == 128000  # every allow-listed Claude model's own output maximum
