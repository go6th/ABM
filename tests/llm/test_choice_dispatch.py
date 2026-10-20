"""The live port with the user's model choice: which provider serves a chosen model, how the
research cache keys a choice, and the fallback to Claude when Kimi K3 cannot meet a schema."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.platform.llm import AnthropicLlm, ChoiceLlm, load_routes
from account_intelligence.platform.ports import LlmResult, LlmUsage
from llm.choice import RunModels, catalogue

pytestmark = pytest.mark.asyncio
OPUS, FABLE, KIMI = catalogue().ids


class FakeMessages:
    def __init__(self) -> None:
        self.models: list[str] = []

    async def create(self, **kw: Any) -> Any:
        self.models.append(kw["model"])
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(
                input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
            model=kw["model"],
            stop_reason="end_turn",
        )  # fmt: skip


class FakeMoonshot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def complete(self, model: str, system: str, user: str, **kw: Any) -> LlmResult:
        self.calls.append((model, kw.get("effort")))
        return LlmResult("ok", model, LlmUsage(1, 1))


def _port() -> tuple[ChoiceLlm, FakeMessages, FakeMoonshot]:
    messages, moonshot = FakeMessages(), FakeMoonshot()
    client = SimpleNamespace(messages=messages)
    return (
        ChoiceLlm(AnthropicLlm(client, load_routes(), "anthropic_api"), moonshot),
        messages,
        moonshot,
    )


async def test_a_chosen_model_reaches_its_provider() -> None:
    port, messages, moonshot = _port()
    await port.complete_as(KIMI, "extractor", "s", "u", max_tokens=100)
    await port.complete_as(FABLE, "extractor", "s", "u", max_tokens=100)
    assert [c[0] for c in moonshot.calls] == [KIMI] and messages.models == [FABLE]
    await port.complete("extractor", "s", "u", max_tokens=100)  # the configured route
    assert messages.models[-1] == load_routes()["extractor"].model


def _deps(port: Any, models: RunModels | None) -> Any:
    from account_intelligence.workflow.deps import RunDeps

    return RunDeps(
        tenant_id=uuid4(), run_id=uuid4(), account_id=uuid4(), domain="x.example",
        txn=None, llm=port, payloads=None, clock=None, spec=None, models=models,
    )  # type: ignore[arg-type]  # fmt: skip


async def test_the_cache_key_keeps_old_replies_for_the_configured_model() -> None:
    port, _, _ = _port()
    configured = load_routes()["extractor"].model
    deps = _deps(port, RunModels(KIMI, FABLE))
    assert deps.chosen_model("extractor") == KIMI and deps.chosen_model("narrative") == FABLE
    assert deps._cache_tier("extractor", configured) == "extractor"  # reused, never paid again
    assert deps._cache_tier("extractor", KIMI) == f"extractor@{KIMI}"
    assert _deps(port, None).chosen_model("extractor") is None


async def test_kimi_falls_back_to_claude_when_it_cannot_meet_the_schema() -> None:
    from pydantic import BaseModel

    from agents.production.wiring import RunDepsModel

    class Out(BaseModel):
        value: int

    served: list[str | None] = []
    stored: list[Any] = []

    class Deps:
        tenant_id, run_id = uuid4(), uuid4()
        models = RunModels(KIMI, FABLE)

        def chosen_model(self, tier: str, override: str | None = None) -> str | None:
            return override or self.models.for_tier(tier)

        async def model(
            self, task_kind: str, system: str, user: str, *, model: str | None = None, **kw: Any
        ) -> LlmResult:
            served.append(model)
            return LlmResult(
                '{"value": 1}' if model == OPUS else "not json", model or KIMI, LlmUsage(1, 1)
            )

        def txn(self, tenant: Any) -> Any:
            class Session:
                async def __aenter__(self) -> Any:
                    return self

                async def __aexit__(self, *a: Any) -> None:
                    return None

                async def execute(self, statement: Any, params: Any) -> None:
                    stored.append(params)

            return Session()

    out, _ = await RunDepsModel(Deps()).call(  # type: ignore[arg-type]
        "abm_pass1_extract",
        "prompt",
        "h",
        {},
        Out,
        None,  # type: ignore[arg-type]
    )
    assert out.value == 1 and served == [None, None, OPUS]
    assert Deps.models.fallbacks == [
        {"step": "abm_pass1_extract", "from": KIMI, "to": OPUS, "reason": "schema_invalid"}
    ]
    assert stored and "schema_invalid" in stored[0]["m"]
