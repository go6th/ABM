"""The Claude API token review of 2026-10-20, item by item, against fake SDK clients (no model is
called, nothing is paid):

* research calls: structured output and a cached source block in the request, the Message
  Batches path (submit once, re-attach from the stored record in a later slice, cancel after
  the wait bound and fall back to one standard call), a refused schema retried without it;
* pricing: half price for a batched call, one-hour cache writes at twice the input price;
* routing: a role left on its default model is served by the active arm, the judge never runs on
  the extractor's model;
* Ask: the whole file is labelled by its document id and sent first, the rules and the tool loop
  are cached, an unknown citation is repaired instead of discarding the paid reply;
* the support judge sees every evidence sentence of a claim in one call.

GUARANTEE(test_a_batched_call_reattaches_instead_of_paying_again)
GUARANTEE(test_an_unknown_citation_no_longer_discards_the_reply)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.platform.llm import (
    AnthropicLlm,
    ModelRoute,
    load_batch_config,
    load_helper,
    load_routes,
    request_kwargs,
)
from account_intelligence.platform.ports import BatchSlot, CallOptions
from llm.reply_cache import ReplyUsage, price

# model ids come from config only (R1: no model id in tests): the cost_saving arm's extractor
# (Sonnet 5.5), its judge (Opus 5.5) and the route classifier's helper (Haiku 5.5)
_ROUTES = load_routes()
EXTRACTOR = _ROUTES["extractor"].model
JUDGE = _ROUTES["judge"].model
_HELPER = load_helper("route_classifier")
assert _HELPER is not None
HELPER = _HELPER.model
ROUTE = ModelRoute("extractor", "anthropic", EXTRACTOR, "low", True)


@dataclass
class _Usage:
    input_tokens: int = 100
    output_tokens: int = 20
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _Block:
    text: str
    type: str = "text"


@dataclass
class _Message:
    text: str
    model: str = EXTRACTOR
    stop_reason: str = "end_turn"
    usage: _Usage = field(default_factory=_Usage)

    @property
    def content(self) -> list[_Block]:
        return [_Block(self.text)]


@dataclass
class _Batch:
    id: str
    processing_status: str


@dataclass
class _Outcome:
    type: str
    message: _Message | None = None


@dataclass
class _Item:
    custom_id: str
    result: _Outcome


class _Results:
    def __init__(self, items: list[_Item]) -> None:
        self.items = items

    def __aiter__(self) -> Any:
        async def gen() -> Any:
            for item in self.items:
                yield item

        return gen()


class _Batches:
    """A scripted Batches API: ``statuses`` are what successive retrieves return."""

    def __init__(self, statuses: list[str], outcome: _Outcome) -> None:
        self.statuses, self.outcome = statuses, outcome
        self.created: list[Any] = []
        self.cancelled: list[str] = []

    async def create(self, *, requests: list[Any]) -> _Batch:
        self.created.append(requests)
        return _Batch("batch_1", "in_progress")

    async def retrieve(self, batch_id: str) -> _Batch:
        status = self.statuses.pop(0) if self.statuses else "ended"
        return _Batch(batch_id, status)

    async def cancel(self, batch_id: str) -> _Batch:
        self.cancelled.append(batch_id)
        return _Batch(batch_id, "canceling")

    async def results(self, batch_id: str) -> _Results:
        return _Results([_Item("call", self.outcome)])


class _Messages:
    def __init__(self, batches: _Batches | None = None, refuse_schema: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.refuse_schema = refuse_schema
        if batches is not None:
            self.batches = batches

    async def create(self, **kw: Any) -> _Message:
        self.calls.append(kw)
        if self.refuse_schema and "format" in (kw.get("output_config") or {}):
            error = RuntimeError("output_config.format: schema is too complex")
            error.status_code = 400  # type: ignore[attr-defined]
            raise error
        return _Message('{"ok": true}')


class _Client:
    def __init__(self, messages: _Messages) -> None:
        self.messages = messages


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _slot(store: dict[str, Any], clock: _Clock, max_wait: float = 1800.0) -> BatchSlot:
    async def load() -> dict[str, Any] | None:
        return store.get("record")

    async def save(record: dict[str, Any]) -> None:
        store["record"] = record

    return BatchSlot(load, save, clock, poll_seconds=0.0001, max_wait_seconds=max_wait)


def test_a_research_request_carries_the_schema_and_the_cached_source() -> None:
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string", "maxLength": 300}},
        "required": ["answer"],
    }
    kw = request_kwargs(ROUTE, "rules", '{"question": "q"}', 4000, CallOptions("SOURCE", schema))
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    first, second = kw["messages"][0]["content"]
    assert first == {"type": "text", "text": "SOURCE", "cache_control": {"type": "ephemeral"}}
    assert second == {"type": "text", "text": '{"question": "q"}'}
    fmt = kw["output_config"]["format"]
    assert fmt["type"] == "json_schema" and kw["output_config"]["effort"] == "low"
    # the SDK's transform closes every object and moves unsupported keywords out
    assert fmt["schema"]["additionalProperties"] is False
    assert "maxLength" not in fmt["schema"]["properties"]["answer"]
    # a batched call caches for an hour (batches finish minutes apart)
    long = request_kwargs(ROUTE, "rules", "u", 10, ttl="1h")
    assert long["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert long["messages"][0]["content"] == "u"


@pytest.mark.asyncio
async def test_a_batched_call_reattaches_instead_of_paying_again() -> None:
    store: dict[str, Any] = {}
    clock = _Clock()
    batches = _Batches(["in_progress", "ended"], _Outcome("succeeded", _Message('{"a": 1}')))
    messages = _Messages(batches)
    llm = AnthropicLlm(_Client(messages), {"extractor": ROUTE}, "anthropic_api")
    result = await llm.complete(
        "extractor", "s", "u", max_tokens=100, options=CallOptions(batch=_slot(store, clock))
    )
    assert result.text == '{"a": 1}' and result.extra["batch"] is True
    assert len(batches.created) == 1 and messages.calls == []
    params = batches.created[0][0]["params"]
    assert params["system"][0]["cache_control"]["ttl"] == "1h"
    assert store["record"]["batch_id"] == "batch_1"
    # a later slice (same request digest, the record kept) re-attaches: nothing is submitted
    batches.statuses = ["ended"]
    again = await llm.complete(
        "extractor", "s", "u", max_tokens=100, options=CallOptions(batch=_slot(store, clock))
    )
    assert again.text == '{"a": 1}' and len(batches.created) == 1


@pytest.mark.asyncio
async def test_a_batch_past_its_wait_is_cancelled_and_asked_once_at_the_standard_price() -> None:
    store: dict[str, Any] = {}
    clock = _Clock()
    batches = _Batches(["in_progress"] * 3 + ["ended"], _Outcome("canceled"))
    messages = _Messages(batches)
    llm = AnthropicLlm(_Client(messages), {"extractor": ROUTE}, "anthropic_api")
    slot = _slot(store, clock, max_wait=60.0)
    store["record"] = {"batch_id": "batch_1", "submitted_at": clock.t - 120.0}
    result = await llm.complete(
        "extractor", "s", "u", max_tokens=100, options=CallOptions(batch=slot)
    )
    assert batches.cancelled == ["batch_1"]
    assert len(messages.calls) == 1 and result.extra["batch"] is False
    assert store["record"] == {}  # the next attempt submits afresh


@pytest.mark.asyncio
async def test_a_refused_schema_is_asked_again_without_it() -> None:
    messages = _Messages(refuse_schema=True)
    llm = AnthropicLlm(_Client(messages), {"extractor": ROUTE}, "anthropic_api")
    schema = {"type": "object", "properties": {"zz_refused": {"type": "string"}}}
    result = await llm.complete(
        "extractor", "s", "u", max_tokens=10, options=CallOptions(schema=schema)
    )
    assert result.text == '{"ok": true}'
    assert len(messages.calls) == 2 and "output_config" in messages.calls[0]
    assert "format" not in messages.calls[1].get("output_config", {})
    # remembered for the process: the next call goes without the format at once
    await llm.complete("extractor", "s", "u", max_tokens=10, options=CallOptions(schema=schema))
    assert len(messages.calls) == 3 and "format" not in messages.calls[2].get("output_config", {})


def test_batched_and_long_cached_calls_are_priced_as_billed() -> None:
    standard = price(EXTRACTOR, ReplyUsage(1_000_000, 100_000))
    batched = price(EXTRACTOR, ReplyUsage(1_000_000, 100_000, batch=True))
    assert standard == Decimal("3") and batched == Decimal("1.5")
    # 1M tokens written: half at the 5-minute rate (2.50), half for an hour (2 x 2.00)
    writes = price(EXTRACTOR, ReplyUsage(0, 0, 0, 1_000_000, 500_000))
    assert writes == Decimal("1.25") + Decimal("2")
    assert price(HELPER, ReplyUsage(1_000_000, 1_000_000)) == Decimal("0.60")


def test_the_config_switches_the_arm_and_lowers_effort() -> None:
    import yaml

    from account_intelligence.config import CONFIG_DIR

    raw = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text())
    assert raw["active_arm"] == "cost_saving"
    profiles = raw["profiles"]
    routes = load_routes()
    assert routes["extractor"].model == profiles["extraction_sonnet"]["model"]
    assert routes["classifier"].model == profiles["extraction_sonnet"]["model"]
    assert routes["judge"].model == profiles["collection_opus"]["model"]
    assert routes["planner"].model == profiles["collection_opus"]["model"]
    assert routes["synthesis"].effort == "medium" and routes["narrative"].effort == "medium"
    assert load_batch_config().applies("abm_pass1_extract")
    assert not load_batch_config().applies("file_answer_question")  # interactive
    assert profiles["helper_haiku"]["model"] == HELPER


def test_a_role_on_its_default_runs_on_the_arm_and_the_judge_stays_independent() -> None:
    from account_intelligence.workflow.deps import RunDeps
    from llm.choice import RunModels, catalogue

    cat = catalogue()

    class Port:
        routes = load_routes()

        async def complete_as(self, *a: Any, **k: Any) -> Any:
            raise AssertionError("not called")

    def deps(models: RunModels) -> Any:
        d = RunDeps.__new__(RunDeps)
        object.__setattr__(d, "llm", Port())
        object.__setattr__(d, "models", models)
        return d

    default = deps(RunModels(cat.default_research, cat.default_analysis))
    for tier in ("planner", "extractor", "classifier", "synthesis", "narrative", "judge"):
        assert default.chosen_model(tier) is None  # the arm's route serves it
    # a picked analysis model serves its tiers; the judge never matches the extractor
    picked = deps(RunModels(cat.default_research, EXTRACTOR))
    assert picked.chosen_model("synthesis") == EXTRACTOR
    judge = picked.chosen_model("judge")
    assert judge is not None and judge != EXTRACTOR


def _row(key: str, text: str = "x", **extra: Any) -> Any:
    from account_intelligence.replica import answers as A

    acct = A.Acct(uuid4(), "acme.com", "Acme", [], None, None, 0)
    return A.Row(key, "file", acct, text, "t", "m", None, None, extra=dict(extra))


def test_the_whole_file_is_labelled_by_its_document_and_stays_byte_identical() -> None:
    from account_intelligence.replica.model_answer import context_block, file_blocks

    doc = _row("whole:doc-1", "a,b\n1,2", whole_file=True, document="Jobs.xlsx", lines=2)
    other = _row("fact:1")
    _, ids_first = context_block([doc, other])
    _, ids_later = context_block([other, other, doc])  # the file is R3 in a later question
    assert file_blocks(ids_first) == file_blocks(ids_later)
    assert '<file doc="doc-1" name="Jobs.xlsx">' in file_blocks(ids_first)
    block, _ = context_block([doc])
    assert 'given whole in <file doc="doc-1">' in block


def test_an_unknown_citation_no_longer_discards_the_reply() -> None:
    from account_intelligence.replica.model_answer import parse, repair

    ids = {"R1": _row("fact:1"), "R2": _row("fact:2")}
    reply = (
        "| Title | Row |\n|---|---|\n| Admin | [R1] |\n| Ghost | [R9] |\n| Both | [R2][R7] |\n\n"
        "A fact [R2]."
    )
    draft = parse(reply, ids)
    assert draft is not None
    text = "\n".join(str(p) for p in draft.paragraphs)
    assert "Ghost" not in text and "Admin" in text and "Both" in text and "R7" not in text
    assert set(draft.rows) == {"fact:1", "fact:2"}
    assert repair("only [R9]", ids) == ("", ["R9"])
    assert parse("nothing known [R9]", ids) is None  # no valid citation left


def test_the_render_payload_drops_items_resting_on_unknown_rows() -> None:
    from account_intelligence.replica.render import rewrite

    ids = {"R1": _row("fact:1")}
    payload = {
        "headline": "Two postings [R1]",
        "sections": [{"rows": [["Admin", "[R1]"], ["Ghost", "[R8]"]]}],
    }
    out = rewrite(payload, ids)
    assert out is not None
    body, rows = out
    assert body["sections"][0]["rows"] == [["Admin", "[[fact:1]]"]]
    assert set(rows) == {"fact:1"}
    assert rewrite({"headline": "x [R8]"}, ids) is None


@pytest.mark.asyncio
async def test_ask_caches_the_file_first_the_rules_and_the_tool_loop() -> None:
    from account_intelligence.chat.service import AnthropicChatProvider, ChatMessage

    captured: list[dict[str, Any]] = []

    class Stream:
        def __init__(self, kw: dict[str, Any]) -> None:
            captured.append(kw)

        async def __aenter__(self) -> Stream:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        def __aiter__(self) -> Any:
            async def gen() -> Any:
                if False:
                    yield None

            return gen()

        async def get_final_message(self) -> Any:
            return type("F", (), {"content": [], "stop_reason": "end_turn", "usage": _Usage()})()

    class Msgs:
        def stream(self, **kw: Any) -> Stream:
            return Stream(kw)

    provider = AnthropicChatProvider(type("C", (), {"messages": Msgs()})())
    model = type("M", (), {"id": JUDGE})()
    tool = {"name": "query_table", "input_schema": {"type": "object"}}
    async for _ in provider.stream(
        model, [ChatMessage("user", "q")], 100, system="RULES", cached="FILE", tools=[tool]
    ):
        pass
    kw = captured[0]
    assert [b["text"] for b in kw["system"]] == ["FILE", "RULES"]
    assert all(b["cache_control"] == {"type": "ephemeral"} for b in kw["system"])
    assert kw["cache_control"] == {"type": "ephemeral"}
    captured.clear()
    async for _ in provider.stream(model, [ChatMessage("user", "q")], 100, context="ws"):
        pass
    # the grounded chat prompt carries this turn's data: not cached, no automatic breakpoint
    assert "cache_control" not in captured[0]["system"][0] and "cache_control" not in captured[0]


@pytest.mark.asyncio
async def test_the_support_judge_sees_every_sentence_of_a_claim_in_one_call() -> None:
    from account_intelligence.grading.config import load_grading_config
    from account_intelligence.grading.judge import call_judge_many

    cfg = load_grading_config()

    class Port:
        model_task = "verification"

        def __init__(self, verdicts: list[bool]) -> None:
            self.verdicts, self.calls = verdicts, 0

        async def judge(self, claim: str, sentence: str) -> Any:
            raise AssertionError("asked one sentence at a time")

        async def judge_many(self, claim: str, sentences: list[str]) -> Any:
            self.calls += 1
            return {"verdicts": [{"supported": v, "reason": "r"} for v in self.verdicts]}

    ok = Port([True, True, True])
    assert (await call_judge_many(ok, "c", ["a", "b", "c"], cfg)).outcome == "supported"
    assert ok.calls == 1
    one_bad = Port([True, False, True])
    assert (await call_judge_many(one_bad, "c", ["a", "b", "c"], cfg)).outcome == "unsupported"
    short = Port([True])  # a verdict count that does not match is invalid, asked again
    result = await call_judge_many(short, "c", ["a", "b"], cfg)
    assert result.outcome == "unavailable" and short.calls == cfg.judge.max_reasks + 1


def test_the_spend_view_migration_is_additive() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "migrations"
    up = (root / "0079_spend_view.sql").read_text()
    assert "security_invoker = true" in up and "GRANT SELECT ON llm_spend_daily TO app_rw" in up
    assert "DROP" not in up
    down = (root / "0079_spend_view.down.sql").read_text()
    assert "DROP VIEW IF EXISTS llm_spend_daily" in down
