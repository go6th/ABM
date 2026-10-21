"""The prompt-cache warm gate and the shared-first prompt layout (2026-10-21): calls that share a
cached prefix let one call warm it before the rest read it; system prompts are sent as cached
parts, most widely shared first, so other task kinds and other files read the shared parts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest
import yaml

from account_intelligence.chat.registry import get_chat_registry
from account_intelligence.chat.service import AnthropicChatProvider, ChatMessage
from account_intelligence.config import CONFIG_DIR
from account_intelligence.platform import llm as llm_mod
from account_intelligence.platform.llm import AnthropicLlm, ModelRoute, request_kwargs
from account_intelligence.platform.ports import BatchSlot, CallOptions
from account_intelligence.platform.prompt_cache import (
    DEFAULT_MIN_CACHE_TOKENS,
    SystemPrompt,
    WarmGate,
    min_cache_tokens,
    prefix_key,
    system_blocks,
)
from agents.skill_context import with_skill_context

_RAW = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text())
OPUS = _RAW["profiles"]["collection_opus"]["model"]
ROUTE = ModelRoute("extractor", "anthropic", OPUS, "low", True)
BIG = "rules " * 1500  # ~9000 chars: well past the 512-token minimum


@dataclass
class _Usage:
    input_tokens: int = 50
    output_tokens: int = 10
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _Block:
    text: str
    type: str = "text"


@dataclass
class _Message:
    usage: _Usage = field(default_factory=_Usage)
    model: str = OPUS
    stop_reason: str = "end_turn"

    @property
    def content(self) -> list[_Block]:
        return [_Block('{"ok": true}')]


class _Stream:
    def __init__(self, owner: _Messages, kw: dict[str, Any]) -> None:
        self.owner, self.kw = owner, kw

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def __aiter__(self) -> Any:
        async def gen() -> Any:
            self.owner.log.append("lead-started")
            yield object()
            await asyncio.sleep(0.05)  # the leader keeps writing after its first event

        return gen()

    async def get_final_message(self) -> _Message:
        self.owner.log.append("lead-done")
        return _Message(_Usage(cache_creation_input_tokens=2000))


class _Batches:
    def __init__(self, owner: _Messages) -> None:
        self.owner = owner
        self.n = 0

    async def create(self, *, requests: list[Any]) -> Any:
        self.n += 1
        self.owner.log.append("batch")
        return type("B", (), {"id": f"b{self.n}", "processing_status": "in_progress"})()

    async def retrieve(self, batch_id: str) -> Any:
        return type("B", (), {"id": batch_id, "processing_status": "ended"})()

    async def results(self, batch_id: str) -> Any:
        outcome = type(
            "O", (), {"type": "succeeded", "message": _Message(_Usage(cache_read_input_tokens=9))}
        )()
        item = type("I", (), {"custom_id": "call", "result": outcome})()

        async def gen() -> Any:
            yield item

        return gen()


class _Messages:
    def __init__(self, *, batches: bool = False) -> None:
        self.log: list[str] = []
        self.calls: list[dict[str, Any]] = []
        if batches:
            self.batches = _Batches(self)

    async def create(self, **kw: Any) -> _Message:
        self.calls.append(kw)
        self.log.append("create")
        return _Message(_Usage(cache_read_input_tokens=2000))

    def stream(self, **kw: Any) -> _Stream:
        self.calls.append(kw)
        return _Stream(self, kw)


class _Client:
    def __init__(self, messages: _Messages) -> None:
        self.messages = messages


def _slot() -> BatchSlot:
    store: dict[str, Any] = {}

    async def load() -> dict[str, Any] | None:
        return store.get("r")

    async def save(record: dict[str, Any]) -> None:
        store["r"] = record

    return BatchSlot(load, save, lambda: 0.0, poll_seconds=0.0)


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> WarmGate:
    fresh = WarmGate(coalesce_s=0.02)
    monkeypatch.setattr(llm_mod, "GATE", fresh)
    return fresh


def test_minimums_come_from_config_and_unknown_models_get_the_strictest() -> None:
    assert min_cache_tokens(OPUS) == 512
    assert min_cache_tokens("unlisted") == DEFAULT_MIN_CACHE_TOKENS


def test_the_prefix_key_covers_the_cached_prefix_only() -> None:
    a = prefix_key(request_kwargs(ROUTE, BIG, "question one", 100))
    b = prefix_key(request_kwargs(ROUTE, BIG, "question two", 100))
    assert a is not None and b is not None and a.key == b.key and a.ttl == "5m"
    other = prefix_key(request_kwargs(ROUTE, BIG + "x", "question one", 100))
    assert other is not None and other.key != a.key
    hour = prefix_key(request_kwargs(ROUTE, BIG, "q", 100, ttl="1h"))
    assert hour is not None and hour.ttl == "1h"
    # too short to cache, or no breakpoint: not gated
    assert prefix_key(request_kwargs(ROUTE, "short", "q", 100)) is None
    plain = ModelRoute("extractor", "anthropic", OPUS, "low", False)
    assert prefix_key(request_kwargs(plain, BIG, "q", 100)) is None


def test_a_cached_document_moves_the_prefix_to_its_block() -> None:
    doc = CallOptions(cached_user="<source>" + "d" * 4000 + "</source>")
    one = prefix_key(request_kwargs(ROUTE, BIG, "claim one", 100, doc))
    two = prefix_key(request_kwargs(ROUTE, BIG, "claim two", 100, doc))
    alone = prefix_key(request_kwargs(ROUTE, BIG, "claim one", 100))
    assert one is not None and two is not None and alone is not None
    assert one.key == two.key != alone.key


def test_a_system_prompt_in_parts_is_a_string_and_cached_blocks() -> None:
    prompt = SystemPrompt(("shared", "", "task"))
    assert prompt == "shared\n\ntask" and prompt.parts == ("shared", "task")
    longer = prompt.extended("schema")
    assert longer.parts == ("shared", "task\n\nschema")
    blocks = request_kwargs(ROUTE, longer, "u", 100)["system"]
    assert [b["text"] for b in blocks] == ["shared", "task\n\nschema"]
    assert all(b["cache_control"] == {"type": "ephemeral"} for b in blocks)
    many = SystemPrompt(("a", "b", "c", "d"))
    merged = system_blocks(many, {"type": "ephemeral"}, reserved=1)
    assert [b["text"] for b in merged] == ["a\n\nb", "c", "d"]


def test_abm_prompts_share_their_leading_parts_across_task_kinds() -> None:
    pass1 = with_skill_context("P1", "abm_pass1_extract")
    pass2 = with_skill_context("P2", "abm_pass2_extract")
    distill = with_skill_context("D", "abm_distill")
    assert pass1.parts[0] == pass2.parts[0] == distill.parts[0]  # type: ignore[attr-defined]
    assert pass1.parts[1] == pass2.parts[1] != distill.parts[1]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_siblings_wait_for_the_leaders_first_event_then_read(gate: WarmGate) -> None:
    messages = _Messages()
    port = AnthropicLlm(_Client(messages), {"extractor": ROUTE}, "anthropic")
    results = await asyncio.gather(
        *(port.complete("extractor", BIG, f"records {i}", max_tokens=100) for i in range(4))
    )
    assert messages.log[0] == "lead-started"
    assert messages.log.count("create") == 3
    # every follower was sent after the leader began, before or after it finished
    assert messages.log.index("create") > messages.log.index("lead-started")
    assert sum(1 for r in results if r.usage.cache_read_input_tokens) == 3


@pytest.mark.asyncio
async def test_a_lone_batched_call_keeps_its_batch(gate: WarmGate) -> None:
    messages = _Messages(batches=True)
    port = AnthropicLlm(_Client(messages), {"extractor": ROUTE}, "anthropic")
    result = await port.complete(
        "extractor", BIG, "records", max_tokens=100, options=CallOptions(batch=_slot())
    )
    assert messages.log == ["batch"] and result.extra["batch"] is True


@pytest.mark.asyncio
async def test_batched_siblings_get_one_standard_leader_then_batch(gate: WarmGate) -> None:
    messages = _Messages(batches=True)
    port = AnthropicLlm(_Client(messages), {"extractor": ROUTE}, "anthropic")
    results = await asyncio.gather(
        *(
            port.complete(
                "extractor",
                BIG,
                f"records {i}",
                max_tokens=100,
                options=CallOptions(batch=_slot()),
            )
            for i in range(3)
        )
    )
    assert messages.log.count("lead-started") == 1 and messages.log.count("batch") == 2
    assert messages.log.index("batch") > messages.log.index("lead-started")
    lead = next(kw for kw in messages.calls if "system" in kw)
    # the leader writes the hour-long entry its batched siblings read
    assert lead["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert sorted(r.extra["batch"] for r in results) == [False, True, True]


@pytest.mark.asyncio
async def test_a_warm_prefix_goes_at_once(gate: WarmGate) -> None:
    messages = _Messages()
    port = AnthropicLlm(_Client(messages), {"extractor": ROUTE}, "anthropic")
    await port.complete("extractor", BIG, "first", max_tokens=100)
    messages.log.clear()
    await port.complete("extractor", BIG, "second", max_tokens=100)
    # warm: no lead, a plain create
    assert messages.log == ["create"]


class _ChatStream:
    def __init__(self) -> None:
        self.text = ""

    async def __aenter__(self) -> _ChatStream:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def __aiter__(self) -> Any:
        async def gen() -> Any:
            if False:
                yield None

        return gen()

    async def get_final_message(self) -> Any:
        return type("M", (), {"usage": _Usage(), "stop_reason": "end_turn", "content": []})()


class _ChatMessages:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def stream(self, **kw: Any) -> _ChatStream:
        self.requests.append(kw)
        return _ChatStream()


@pytest.mark.asyncio
async def test_ask_sends_the_shared_rules_first_then_the_file_then_the_route_rules() -> None:
    messages = _ChatMessages()
    provider = AnthropicChatProvider(type("C", (), {"messages": messages})())
    model = get_chat_registry().model(None)
    events = provider.stream(
        model,
        [ChatMessage("user", "q")],
        100,
        system=SystemPrompt(("RULES AND SKILL", "ROUTE RULES")),
        cached="FILE",
        tools=[{"name": "t", "input_schema": {"type": "object"}}],
    )
    async for _ in events:
        pass
    system = messages.requests[0]["system"]
    assert [b["text"] for b in system] == ["RULES AND SKILL", "FILE", "ROUTE RULES"]
    assert system[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert system[1]["cache_control"] == system[2]["cache_control"] == {"type": "ephemeral"}


class _FailingStream(_Stream):
    def __aiter__(self) -> Any:
        async def gen() -> Any:
            await asyncio.sleep(0.01)
            raise RuntimeError("connection reset before the first event")
            yield None  # pragma: no cover - makes this an async generator

        return gen()


class _StallingStream(_Stream):
    def __aiter__(self) -> Any:
        async def gen() -> Any:
            await asyncio.sleep(0.3)  # longer than the followers' bounded wait
            self.owner.log.append("lead-started")
            yield object()

        return gen()


class _ScriptedMessages(_Messages):
    def __init__(self, stream_cls: type[_Stream]) -> None:
        super().__init__()
        self.stream_cls = stream_cls

    def stream(self, **kw: Any) -> _Stream:
        self.calls.append(kw)
        return self.stream_cls(self, kw)


@pytest.mark.asyncio
async def test_a_failed_leader_fails_alone_and_frees_its_siblings(gate: WarmGate) -> None:
    messages = _ScriptedMessages(_FailingStream)
    port = AnthropicLlm(_Client(messages), {"extractor": ROUTE}, "anthropic")
    results = await asyncio.wait_for(
        asyncio.gather(
            *(port.complete("extractor", BIG, f"r{i}", max_tokens=100) for i in range(3)),
            return_exceptions=True,
        ),
        timeout=5,
    )
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1 and "connection reset" in str(errors[0])
    assert messages.log.count("create") == 2  # the siblings were sent, not stranded


@pytest.mark.asyncio
async def test_a_stalled_leader_holds_its_siblings_only_for_the_bounded_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_mod, "GATE", WarmGate(max_wait_s=0.05))
    messages = _ScriptedMessages(_StallingStream)
    port = AnthropicLlm(_Client(messages), {"extractor": ROUTE}, "anthropic")
    await asyncio.gather(
        *(port.complete("extractor", BIG, f"r{i}", max_tokens=100) for i in range(3))
    )
    # both siblings gave up waiting and were sent before the leader's first event
    assert messages.log.index("create") < messages.log.index("lead-started")


@pytest.mark.asyncio
async def test_the_kill_switch_sends_every_call_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "GATE", WarmGate(enabled=False))
    messages = _Messages()
    port = AnthropicLlm(_Client(messages), {"extractor": ROUTE}, "anthropic")
    await asyncio.gather(
        *(port.complete("extractor", BIG, f"r{i}", max_tokens=100) for i in range(3))
    )
    assert messages.log == ["create", "create", "create"]


def test_the_gate_settings_are_checked_at_start_up(tmp_path: Any) -> None:
    from account_intelligence.platform.prompt_cache import gate_from_config

    live = gate_from_config()
    assert live.enabled is True and live.coalesce_s == 0.25 and live.max_wait_s == 120.0
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump({}))
    assert gate_from_config(path).enabled is True  # absent: on, with the defaults
    for bad in ({"enable": True}, {"max_wait_seconds": 0}, {"coalesce_seconds": 60}):
        path.write_text(yaml.safe_dump({"prompt_cache_gate": bad}))
        with pytest.raises(ValueError):
            gate_from_config(path)


def test_the_read_ratio_covers_the_rolling_window_only() -> None:
    from account_intelligence.platform.prompt_cache import ReadRatio

    now = [0.0]
    ratio = ReadRatio(window_s=60, clock=lambda: now[0])
    assert ratio.add("extractor", 0, 100, 0) == 0.0
    now[0] = 30
    assert ratio.add("extractor", 300, 0, 0) == 0.75
    now[0] = 100  # the first two calls left the window
    assert ratio.add("extractor", 50, 0, 50) == 0.5
    assert ratio.add("judge", 0, 0, 0) is None


@pytest.mark.asyncio
async def test_every_call_logs_and_records_its_cache_tokens(
    gate: WarmGate, monkeypatch: pytest.MonkeyPatch
) -> None:
    import obs.setup

    recorded: list[tuple[str, float, dict[str, Any]]] = []
    monkeypatch.setattr(
        obs.setup, "record", lambda name, value, labels=None: recorded.append((name, value, labels))
    )
    port = AnthropicLlm(_Client(_Messages()), {"extractor": ROUTE}, "anthropic")
    await port.complete("extractor", BIG, "first", max_tokens=100)  # leads: writes 2000
    await port.complete("extractor", BIG, "second", max_tokens=100)  # warm: reads 2000
    tokens = [(v, lab["token_kind"]) for n, v, lab in recorded if n == "llm_tokens_total"]
    assert (2000, "cache_write") in tokens and (2000, "cache_read") in tokens
    ratios = [v for n, v, _ in recorded if n == "cache_read_ratio"]
    assert ratios and 0 < ratios[-1] < 1
