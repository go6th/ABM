"""The live port with the user's model choice: which provider serves a chosen model, how the
research cache keys a choice, and the fallback to Claude when Kimi K3 cannot meet a schema."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.platform.llm import AnthropicLlm, ChoiceLlm, load_routes
from account_intelligence.platform.ports import LlmResult, LlmUsage
from llm.choice import RunModels, catalogue

pytestmark = pytest.mark.asyncio
OPUS, FABLE, KIMI, SONNET, HAIKU, GROK = catalogue().ids


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


async def test_grok_is_served_by_the_xai_client_with_the_chosen_effort() -> None:
    """Grok 4.5 (owner decision 2026-10-20) goes to the xAI client, never the Anthropic port or
    the Moonshot client, with the user's effort for the role (else the tier's) and the source
    document folded into the user text (no cache blocks); without its key it is refused."""
    from account_intelligence.platform.ports import CallOptions, NotConfigured

    messages, moonshot, xai = FakeMessages(), FakeMoonshot(), FakeMoonshot()
    client = SimpleNamespace(messages=messages)
    port = ChoiceLlm(AnthropicLlm(client, load_routes(), "anthropic_api"), moonshot, xai)
    await port.complete_as(
        GROK, "extractor", "s", "u", max_tokens=100,
        options=CallOptions(cached_user="doc", effort="high"),
    )  # fmt: skip
    await port.complete_as(GROK, "synthesis", "s", "u", max_tokens=100)
    assert xai.calls == [(GROK, "high"), (GROK, load_routes()["synthesis"].effort)]
    assert moonshot.calls == [] and messages.models == []
    with pytest.raises(NotConfigured, match="XAI_API_KEY"):
        await ChoiceLlm(AnthropicLlm(client, load_routes(), "anthropic_api"), moonshot).complete_as(
            GROK, "extractor", "s", "u", max_tokens=100
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
    events: list[Any] = []

    class Deps:
        tenant_id, run_id = uuid4(), uuid4()
        models = RunModels(KIMI, FABLE)
        clock = SimpleNamespace(now=lambda: datetime(2026, 11, 3, tzinfo=UTC))

        def chosen_model(self, tier: str, override: str | None = None) -> str | None:
            return override or self.models.for_tier(tier)

        async def model(
            self, task_kind: str, system: str, user: str, *, model: str | None = None, **kw: Any
        ) -> LlmResult:
            served.append(model)
            return LlmResult(
                '{"value": 1}' if model == OPUS else "not json",
                model or KIMI,
                LlmUsage(1_000_000, 0),
            )

        def txn(self, tenant: Any) -> Any:
            class Session:
                async def __aenter__(self) -> Any:
                    return self

                async def __aexit__(self, *a: Any) -> None:
                    return None

                async def execute(self, statement: Any, params: Any) -> Any:
                    if "usage_events" in str(statement):
                        events.append(params)
                    else:
                        stored.append(params)
                    return type("R", (), {"first": lambda self: (1,)})()

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
    # the fallback is a billing event (ADR-0119): both models, what the discarded attempts on
    # the chosen model cost and what the Claude calls that replaced them added to the bill
    from llm.cost import price_of

    kimi_in, opus_in = price_of(KIMI).input, price_of(OPUS).input  # type: ignore[union-attr]
    assert Deps.models.fallbacks == [
        {
            "step": "abm_pass1_extract",
            "from": KIMI,
            "to": OPUS,
            "reason": "schema_invalid",
            "discarded_usd": str(2 * kimi_in),
            "extra_usd": str(opus_in),
        }
    ]
    assert stored and "schema_invalid" in stored[0]["m"]
    # recorded on the run when it fell back, and again once the extra cost is known
    assert len(stored) == 2 and '"extra_usd"' in stored[-1]["m"]
    # and billed as one ``model_fallback`` usage event with both model ids and the extra cost,
    # keyed by the request so a replayed step adds nothing (migration 0100)
    assert len(events) == 1 and events[0]["k"] == "model_fallback" and events[0]["u"] == 1
    assert events[0]["s"].startswith(f"{Deps.run_id}:abm_pass1_extract:")
    assert events[0]["key"] == f"model_fallback:{events[0]['s']}"
    assert json.loads(events[0]["d"]) == Deps.models.fallbacks[0]


class _Payloads:
    """An in-memory payload store (no reply stored yet: every call reaches the port)."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes:
        from account_intelligence.platform.ports import PayloadMissing

        if key not in self.data:
            raise PayloadMissing(key)
        return self.data[key]

    async def put(self, key: str, value: bytes) -> None:
        self.data[key] = value


class _EffortPort:
    """A port that records the effort each tier was asked at (None: the task's own)."""

    supports_call_options = True

    def __init__(self) -> None:
        self.routes = load_routes()
        self.asked: dict[str, str | None] = {}

    async def complete(self, tier: str, system: str, user: str, **kw: Any) -> LlmResult:
        options = kw.get("options")
        self.asked[tier] = options.effort if options is not None else None
        return LlmResult("{}", self.routes[tier].model, LlmUsage(1, 1))

    async def complete_as(self, model: str, tier: str, *a: Any, **kw: Any) -> LlmResult:
        return await self.complete(tier, *a, **kw)


async def test_the_users_effort_never_reaches_extraction_or_classification() -> None:
    """A user's effort serves the judgment tiers of its role (planning, which runs the sense
    check and distill, and the analysis tiers); extraction and classification keep their
    configured effort (low), so a run that stored "high" does not pay extraction at high."""
    port = _EffortPort()
    deps = _deps(port, RunModels(OPUS, OPUS, research_effort="high", analysis_effort="high"))
    deps.payloads = _Payloads()
    for kind in ("abm_pass1_extract", "classify_posting", "abm_sense_check", "support_judge"):
        await deps.model(kind, "s", f"u {kind}")
    assert port.asked == {"extractor": None, "classifier": None, "planner": "high", "judge": "high"}
    assert load_routes()["extractor"].effort == "low"
    # an agent whose registry entry names an effort runs at it (config/agents.yaml ``effort``)
    await deps.model("abm_people_extract", "s", "u people", effort="medium")
    assert port.asked["extractor"] == "medium"
