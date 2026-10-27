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

from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
import yaml

from account_intelligence.config import CONFIG_DIR
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

# model ids come from config only (R1: no model id in tests): the Opus 5.5 and Sonnet 5.5
# profiles of models.yaml and the route classifier's helper (Haiku 5.5)
_RAW = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text())
OPUS = _RAW["profiles"]["collection_opus"]["model"]
SONNET = _RAW["profiles"]["extraction_sonnet"]["model"]
_ROUTES = load_routes()
_HELPER = load_helper("route_classifier")
assert _HELPER is not None
HELPER = _HELPER.model
ROUTE = ModelRoute("extractor", "anthropic", SONNET, "low", True)


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
    model: str = SONNET
    stop_reason: str = "end_turn"
    usage: _Usage = field(default_factory=_Usage)

    @property
    def content(self) -> list[_Block]:
        return [_Block(self.text)]


@dataclass
class _Counts:
    processing: int = 1


@dataclass
class _Batch:
    id: str
    processing_status: str
    request_counts: _Counts = field(default_factory=_Counts)


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


# a real Message Batches id shape: it must survive the log scrubber's token filter
BATCH_ID = "msgbatch_013Zva2CMHLNnXjNJJKqJ2EF"


def _scrubbed(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, Any]]:
    """The JSON lines of one event as the production formatter writes them (allow-list and
    shape filter applied)."""
    import json

    from obs.catalog import load_catalog
    from obs.exporters import DropCounter
    from obs.logging import JsonFormatter

    fmt = JsonFormatter(load_catalog(), DropCounter(), "worker")
    lines = [json.loads(fmt.format(r)) for r in caplog.records if r.getMessage() == event]
    return lines


@pytest.fixture
def _fast_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    from account_intelligence.platform import llm as llm_module

    monkeypatch.setattr(llm_module, "BATCH_PACER", llm_module.RequestPacer(0))
    monkeypatch.setattr(llm_module, "WAIT_LOG_S", 0.0)  # a waiting line on every poll


@pytest.mark.asyncio
@pytest.mark.usefixtures("_fast_batches")
async def test_a_batch_wait_logs_its_progress_and_its_finish(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store: dict[str, Any] = {"record": {"batch_id": BATCH_ID, "submitted_at": 900.0}}
    batches = _Batches(
        ["in_progress", "in_progress", "ended"], _Outcome("succeeded", _Message("x"))
    )
    llm = AnthropicLlm(_Client(_Messages(batches)), {"extractor": ROUTE}, "anthropic_api")
    caplog.set_level("INFO", logger="platform.llm")
    await llm.complete(
        "extractor", "s", "u", max_tokens=10, options=CallOptions(batch=_slot(store, _Clock()))
    )
    assert _scrubbed(caplog, "llm.batch_reattached")[0]["batch_id"] == BATCH_ID
    waiting = _scrubbed(caplog, "llm.batch_waiting")
    assert [w["polls"] for w in waiting] == [1, 2]
    first = waiting[0]
    assert first["batch_id"] == BATCH_ID and first["waited_s"] == 100
    assert first["state"] == "in_progress" and first["count"] == 1
    assert first["model"] == SONNET and first["tier"] == "extractor"
    (done,) = _scrubbed(caplog, "llm.batch_finished")
    assert done["batch_id"] == BATCH_ID and done["outcome"] == "succeeded" and done["polls"] == 3


@pytest.mark.asyncio
@pytest.mark.usefixtures("_fast_batches")
async def test_a_failed_poll_is_logged_and_raised(caplog: pytest.LogCaptureFixture) -> None:
    class Broken(_Batches):
        async def retrieve(self, batch_id: str) -> _Batch:
            if not self.statuses:
                raise ConnectionError("reset by peer")
            return await super().retrieve(batch_id)

    store: dict[str, Any] = {"record": {"batch_id": BATCH_ID, "submitted_at": 1000.0}}
    batches = Broken(["in_progress"], _Outcome("succeeded", _Message("x")))
    llm = AnthropicLlm(_Client(_Messages(batches)), {"extractor": ROUTE}, "anthropic_api")
    with pytest.raises(ConnectionError):
        await llm.complete(
            "extractor", "s", "u", max_tokens=10, options=CallOptions(batch=_slot(store, _Clock()))
        )
    (failed,) = _scrubbed(caplog, "llm.batch_poll_failed")
    assert failed["batch_id"] == BATCH_ID and failed["polls"] == 1
    assert failed["error_class"] == "ConnectionError" and failed["operation"] == "retrieve"
    assert failed["level"] == "WARNING" and _scrubbed(caplog, "llm.batch_finished") == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("_fast_batches")
async def test_a_cancelled_batch_wait_is_logged_and_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import asyncio

    store: dict[str, Any] = {"record": {"batch_id": BATCH_ID, "submitted_at": 1000.0}}
    batches = _Batches(["in_progress"] * 100_000, _Outcome("succeeded", _Message("x")))
    llm = AnthropicLlm(_Client(_Messages(batches)), {"extractor": ROUTE}, "anthropic_api")
    slot = _slot(store, _Clock())
    task = asyncio.ensure_future(
        llm.complete("extractor", "s", "u", max_tokens=10, options=CallOptions(batch=slot))
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    (gone,) = _scrubbed(caplog, "llm.batch_wait_cancelled")
    assert gone["batch_id"] == BATCH_ID and gone["polls"] >= 1


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
    standard = price(SONNET, ReplyUsage(1_000_000, 100_000))
    batched = price(SONNET, ReplyUsage(1_000_000, 100_000, batch=True))
    assert standard == Decimal("3") and batched == Decimal("1.5")
    # 1M tokens written: half at the 5-minute rate (2.50), half for an hour (2 x 2.00)
    writes = price(SONNET, ReplyUsage(0, 0, 0, 1_000_000, 500_000))
    assert writes == Decimal("1.25") + Decimal("2")
    assert price(HELPER, ReplyUsage(1_000_000, 1_000_000)) == Decimal("0.60")


def test_the_config_switches_the_arm_and_lowers_effort() -> None:
    raw = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text())
    # the owner's split (reverted to on 2026-10-20): Opus 5.5 extracts, Fable 5.1 judges
    assert raw["active_arm"] == "automatic"
    profiles = raw["profiles"]
    routes = load_routes()
    for tier in ("planner", "extractor", "classifier"):
        assert routes[tier].model == profiles["collection_opus"]["model"]
    assert routes["judge"].model == profiles["analysis_fable"]["model"]
    assert routes["judge"].model != routes["extractor"].model
    assert routes["synthesis"].effort == "medium" and routes["narrative"].effort == "medium"
    economy = replace(load_batch_config(), mode="economy")
    assert economy.applies("abm_pass1_extract")
    assert not economy.applies("source_reread")  # a short verdict a run waits on
    assert not economy.applies("file_answer_question")  # interactive
    assert load_batch_config().mode == "fast" and not load_batch_config().applies(
        "abm_pass1_extract"
    )
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
    picked = deps(RunModels(cat.default_research, SONNET))
    assert picked.chosen_model("synthesis") == SONNET
    assert picked.chosen_model("judge") == SONNET  # differs from the arm's Opus extractor
    # a research pick equal to the arm's judge (Fable 5.1) never judges its own extraction
    fable = _RAW["profiles"]["analysis_fable"]["model"]
    same = deps(RunModels(fable, cat.default_analysis))
    assert same.chosen_model("extractor") == fable
    judge = same.chosen_model("judge")
    assert judge is not None and judge != fable


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
    model = type("M", (), {"id": OPUS})()
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


@pytest.mark.asyncio
async def test_the_users_effort_replaces_the_tasks_on_every_model() -> None:
    """Owner decision 2026-10-20: the user picks medium or high beside each model; no model or
    task has a fixed effort any more."""
    from account_intelligence.chat.service import AnthropicChatProvider, ChatMessage
    from account_intelligence.platform.llm import model_efforts

    assert model_efforts() == {}  # nothing fixed per model
    extract = _ROUTES["extractor"]
    assert extract.effort == "low"  # the task's own effort, when the user chose none
    assert request_kwargs(extract, "s", "u", 10)["output_config"]["effort"] == "low"
    for chosen in ("medium", "high"):
        kw = request_kwargs(extract, "s", "u", 10, CallOptions(effort=chosen))
        assert kw["output_config"]["effort"] == chosen

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

    provider = AnthropicChatProvider(type("C", (), {"messages": Msgs()})(), effort="medium")
    for model_id, effort in ((OPUS, "high"), (SONNET, None), (HELPER, "high")):
        model = type("M", (), {"id": model_id})()
        async for _ in provider.stream(model, [ChatMessage("user", "q")], 100, effort=effort):
            pass
    # the turn's choice wins; without one the surface's effort stands
    assert [c["output_config"]["effort"] for c in captured] == ["high", "medium", "high"]


@pytest.mark.asyncio
async def test_a_run_applies_each_roles_effort_and_keys_its_replies_by_it() -> None:
    from account_intelligence.platform.hermetic import MemoryPayloadStore
    from account_intelligence.workflow.deps import RunDeps
    from llm.choice import RunModels, catalogue

    cat = catalogue()
    seen: list[tuple[str, str | None]] = []

    class Port:
        supports_call_options = True
        routes = load_routes()

        async def complete(self, tier: str, system: str, user: str, **kw: Any) -> Any:
            from account_intelligence.platform.ports import LlmResult, LlmUsage

            options = kw.get("options")
            seen.append((tier, options.effort if options else None))
            return LlmResult('{"ok": 1}', OPUS, LlmUsage(1, 1))

        async def complete_as(self, *a: Any, **k: Any) -> Any:
            raise AssertionError("defaults run on the arm")

    def deps(models: RunModels | None) -> Any:
        d = RunDeps.__new__(RunDeps)
        for name, value in {
            "llm": Port(),
            "models": models,
            "router": None,
            "reply_cache": None,
            "txn": None,
            "payloads": MemoryPayloadStore(),
            "run_input": {},
            "tenant_id": uuid4(),
            "account_id": uuid4(),
            "run_id": uuid4(),
        }.items():
            object.__setattr__(d, name, value)
        return d

    chosen = RunModels(
        cat.default_research, cat.default_analysis, research_effort="high", analysis_effort="medium"
    )
    run = deps(chosen)
    await run.model("abm_pass1_extract", "s", "u")  # research role
    await run.model("support_judge", "s", "u")  # analysis role
    assert seen == [("extractor", "high"), ("judge", "medium")]
    # the same request at another effort is another reply (trying high never reuses medium)
    await run.model("abm_pass1_extract", "s", "u")
    assert len(seen) == 2  # same effort: replayed within the run
    run.models.research_effort = "medium"
    await run.model("abm_pass1_extract", "s", "u")
    assert seen[-1] == ("extractor", "medium") and len(seen) == 3
    # a run from before the choice keeps each task's own effort
    old = deps(None)
    await old.model("abm_pass1_extract", "s", "u")
    assert seen[-1] == ("extractor", None)
    assert RunModels.from_json({"research_effort": "turbo"}).research_effort is None
