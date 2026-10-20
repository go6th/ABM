"""RouteGuard (R1 to R12) with a scripted model and in-memory stores."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel

from llm import cache
from llm.adapters.fake import ScriptedAdapter
from llm.budget import check, estimate_tokens
from llm.config import RoutingConfig, build, load_routing_config
from llm.cost import cost_usd
from llm.errors import (
    AuthFailed,
    BudgetRefused,
    ConfigInvalid,
    ContentRefused,
    ContextTooLong,
    DataClassRefused,
    JudgeNotIndependent,
    ModelUnavailable,
    NoEligibleCandidate,
    Overloaded,
    SchemaInvalid,
    UnknownTaskKind,
)
from llm.health import Health, InMemoryHealth, step
from llm.models import Attempt, Scope, Usage
from llm.port import Router
from llm.prompts import load, prompt_hash
from llm.record import InMemoryCallStore
from llm.routing import select
from tests.llm.conftest import raw_config

T0 = datetime(2026, 1, 1, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


class Out(BaseModel):
    """A synthetic output schema."""

    answer: str


def scope(**kw: Any) -> Scope:
    return Scope(uuid4(), uuid4(), **kw)


def router(
    cfg: RoutingConfig, script: Any
) -> tuple[Router, ScriptedAdapter, InMemoryCallStore, InMemoryHealth]:
    adapter = ScriptedAdapter(script)
    store = InMemoryCallStore()
    health = InMemoryHealth(cfg.health_threshold, cfg.health_window_s, cfg.health_cooldown_s)
    return Router(cfg, {"fake": adapter}, store, health, lambda: T0), adapter, store, health


def test_routing_config_loads_and_every_candidate_fact_is_sourced() -> None:
    real = load_routing_config()
    assert set(real.task_kinds) >= {
        "extract_facts",
        "classify_posting",
        "write_narrative",
        "support_judge",
        "source_reread",
        "narrative_reread",
    }
    assert real.task_kinds["extract_facts"].tier == "extractor"
    assert real.task_kinds["synthesise_hypotheses"].tier == "planner"
    assert real.task_kinds["source_reread"].tier == real.judge_tier
    assert all(t.ladder for t in real.tiers.values())
    assert any("price unknown" in w for w in real.warnings())  # surfaced, never defaulted
    assert len(real.config_hash) == 64


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("candidates", "small", "adapter"), "mystery"),
        (("candidates", "small", "data_classes"), {"value": [], "status": "x"}),
        (("candidates", "small", "context_window"), {"value": 10}),
        (("candidates", "small", "price"), {"input_usd_per_mtok": "-1", "source": "x"}),
        (("tiers", "value", "extractor", "ladder"), ["ghost"]),
        (("task_kinds", "value", "extract_facts", "tier"), "nowhere"),
        (("schema_reasks",), {"value": 3, "source": "x"}),
    ],
)
def test_invalid_routing_config_is_refused(path: tuple[str, ...], value: Any) -> None:
    data = raw_config()
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ConfigInvalid):
        build(data, b"x")


MODEL_ID = re.compile(r"\b(claude|gpt|gemini|llama|mistral|grok)-[0-9a-z][0-9a-z.\-]*", re.I)


def test_no_model_id_outside_config() -> None:
    """R1: no model-id-shaped string in src, prompts or tests."""
    roots = [ROOT / "src", ROOT / "tests", ROOT / "evals"]
    offenders = [
        str(p.relative_to(ROOT))
        for r in roots
        for p in r.rglob("*")
        if p.suffix in (".py", ".md") and "/rag/" not in str(p) and MODEL_ID.search(p.read_text())
    ]
    assert offenders == []


def test_task_kind_is_mandatory(cfg: RoutingConfig) -> None:
    with pytest.raises(UnknownTaskKind):
        select(cfg, "free_form", [], frozenset(), "public_web")


@given(
    st.lists(
        st.builds(
            Attempt,
            candidate=st.sampled_from(["small", "big"]),
            error_class=st.sampled_from(["Overloaded", "ModelUnavailable"]),
        ),
        max_size=5,
    ),
    st.frozensets(st.sampled_from(["small", "big"])),
)
def test_router_choice_is_a_pure_function_of_config_attempt_and_health(
    history: list[Attempt], open_: frozenset[str]
) -> None:
    cfg = build(raw_config(), b"synthetic")
    try:
        one = select(cfg, "extract_facts", history, open_, "public_web")
    except NoEligibleCandidate:
        with pytest.raises(NoEligibleCandidate):
            select(cfg, "extract_facts", history, open_, "public_web")
        return
    assert select(cfg, "extract_facts", list(history), frozenset(open_), "public_web") == one
    assert one.candidate.name not in open_
    assert one.degraded == (one.candidate.name != "small")


def test_fallback_happens_only_on_the_allowed_classes(cfg: RoutingConfig) -> None:
    first = select(cfg, "extract_facts", [], frozenset(), "public_web")
    assert first.candidate.name == "small" and not first.degraded
    one_429 = [Attempt("small", "Overloaded")]
    assert (
        select(cfg, "extract_facts", one_429, frozenset(), "public_web").candidate.name == "small"
    )
    two = one_429 * 2
    nxt = select(cfg, "extract_facts", two, frozenset(), "public_web")
    assert nxt.candidate.name == "big" and nxt.degraded and nxt.reason == "Overloaded"
    gone = select(
        cfg, "extract_facts", [Attempt("small", "ModelUnavailable")], frozenset(), "public_web"
    )
    assert gone.candidate.name == "big"
    longer = select(
        cfg, "extract_facts", [Attempt("small", "ContextTooLong")], frozenset(), "public_web"
    )
    assert longer.candidate.name == "big"  # a larger, known window
    with pytest.raises(NoEligibleCandidate):
        select(cfg, "extract_facts", [Attempt("big", "ContextTooLong")], frozenset(), "public_web")
    for never in ("SchemaInvalid", "AuthFailed", "ContentRefused"):
        with pytest.raises(ValueError, match="never falls back"):
            select(cfg, "extract_facts", [Attempt("small", never)], frozenset(), "public_web")


def test_data_class_refusal_and_judge_independence() -> None:
    cfg = build(raw_config(), b"synthetic")
    with pytest.raises(DataClassRefused):
        select(cfg, "extract_facts", [], frozenset(), "tenant")  # no verified zero retention
    data = raw_config()
    data["candidates"]["big"] = {
        **data["candidates"]["big"],
        "zero_retention": {"value": True, "source": "synthetic"},
        "data_classes": {"value": ["public_web", "tenant"], "status": "x"},
    }
    ok = select(build(data, b"x"), "extract_facts", [], frozenset(), "tenant")
    assert ok.candidate.name == "big"
    judged = select(cfg, "support_judge", [], frozenset(), "public_web", generator_family="fam_a")
    assert judged.candidate.family == "fam_b"  # never the generator's family
    data = raw_config()
    data["tiers"]["value"]["judge"]["ladder"] = ["big"]
    with pytest.raises(JudgeNotIndependent):
        select(
            build(data, b"x"),
            "support_judge",
            [],
            frozenset(),
            "public_web",
            generator_family="fam_a",
        )
    with pytest.raises(NoEligibleCandidate):
        select(cfg, "write_narrative", [], frozenset({"big"}), "public_web")


@pytest.mark.asyncio
async def test_router_end_to_end_replays_records_and_degrades(cfg: RoutingConfig) -> None:
    replies: list[Any] = [Overloaded("429"), Overloaded("429"), '{"answer": "x"}']
    r, adapter, store, _ = router(cfg, lambda c, s, u: replies.pop(0))
    text, phash = "Extract the value.", prompt_hash("Extract the value.")
    sc = scope()
    for _ in range(2):
        with pytest.raises(Overloaded):  # the node RetryPolicy retries; the router does not
            await r.call("extract_facts", text, phash, {"q": 1}, Out, sc)
    routed = await r.call("extract_facts", text, phash, {"q": 1}, Out, sc)
    assert routed.output == Out(answer="x") and not routed.replayed
    assert routed.record.candidate == "big" and routed.record.degraded  # R5
    assert routed.record.cost_usd is not None and routed.record.prompt_hash == phash
    calls = len(adapter.calls)
    again = await r.call("extract_facts", text, phash, {"q": 1}, Out, sc)
    assert again.replayed and len(adapter.calls) == calls  # R9: never pays twice


@pytest.mark.asyncio
async def test_schema_validation_refuses_every_malformed_output(cfg: RoutingConfig) -> None:
    for bad in (
        "not json",
        '{"answer": 3}',
        '{"other": "x"}',
        "[1, 2]",
        "{}",
    ):
        replies = [bad, bad]
        r, adapter, store, _ = router(cfg, lambda c, s, u, rp=replies: rp.pop(0))
        with pytest.raises(SchemaInvalid):
            await r.call("extract_facts", "p", "h", {"q": bad}, Out, scope())
        assert len(adapter.calls) == 2  # exactly one re-ask
        assert store.calls == {}  # nothing unvalidated is stored
    replies = ["nonsense", '{"answer": "fixed"}']
    r, _, _, _ = router(cfg, lambda c, s, u: replies.pop(0))
    assert (await r.call("extract_facts", "p", "h", {}, Out, scope())).output.answer == "fixed"


@pytest.mark.asyncio
async def test_no_fallback_after_authentication_or_content_refusal(cfg: RoutingConfig) -> None:
    for exc in (AuthFailed("no"), ContentRefused("no")):
        r, _, store, _ = router(cfg, lambda c, s, u, e=exc: e)
        with pytest.raises(type(exc)):
            await r.call("extract_facts", "p", "h", {"k": 1}, Out, scope())
        with pytest.raises(ValueError, match="never falls back"):
            await r.call("extract_facts", "p", "h", {"k": 1}, Out, scope())


@pytest.mark.asyncio
async def test_model_unavailable_and_context_too_long_select_another(cfg: RoutingConfig) -> None:
    for first in (ModelUnavailable("gone"), ContextTooLong("long")):
        replies: list[Any] = [first, '{"answer": "y"}']
        r, adapter, _, _ = router(cfg, lambda c, s, u, rp=replies: rp.pop(0))
        with pytest.raises(type(first)):
            await r.call("extract_facts", "p", "h", {"k": 1}, Out, scope())
        routed = await r.call("extract_facts", "p", "h", {"k": 1}, Out, scope())
        assert routed.record.candidate == "big" and routed.record.degraded


def test_budget_refuses_before_the_call() -> None:
    est = estimate_tokens("prefix", "user", 50)
    assert est == len(b"prefix") + len(b"user") + 50
    assert check(0, est, None).status == "unset"
    assert check(10, est, 1000).status == "ok"
    with pytest.raises(BudgetRefused):
        check(990, est, 1000)


@pytest.mark.asyncio
async def test_router_refuses_over_budget_without_calling(cfg: RoutingConfig) -> None:
    from dataclasses import replace

    tight = replace(cfg, token_caps={"standard": 10})
    r, adapter, _, _ = router(tight, lambda c, s, u: '{"answer": "x"}')
    with pytest.raises(BudgetRefused):
        await r.call("extract_facts", "p", "h", {"k": "v"}, Out, scope())
    assert adapter.calls == []


def test_unknown_price_is_none_never_zero(cfg: RoutingConfig) -> None:
    u = Usage(1_000_000, 500_000)
    assert cost_usd(cfg.candidates["small"].price, u) == Decimal("2.000000")
    assert cost_usd(cfg.candidates["other"].price, u) is None


def test_prefix_bytes_are_deterministic_and_carry_no_tenant_data() -> None:
    tools = [{"name": "b", "x": 1}, {"name": "a", "y": {"z": 1, "w": 2}}]
    one = cache.build("System.", tools, "Reference.")
    two = cache.build("System.", list(reversed(tools)), "Reference.")
    assert one == two and one.encode() == two.encode()
    with pytest.raises(cache.PrefixNotStatic):
        cache.build(f"Run {uuid4()}")
    with pytest.raises(cache.PrefixNotStatic):
        cache.build("Generated 2026-10-06T10:00")
    assert cache.cache_ratio([(True, Usage(100, 1, 70, 0)), (False, Usage(100, 1, 0, 0))]) == 0.7
    assert cache.cache_ratio([(False, Usage(10, 1))]) is None


def test_prompt_hash_changes_iff_the_prompt_changes() -> None:
    text, h = load("judge.v1")
    assert h == prompt_hash(text) and prompt_hash(text + " ") != h


@given(st.lists(st.sampled_from(["failure", "success"]), max_size=12))
def test_health_opens_after_threshold_and_closes_after_success(events: list[str]) -> None:
    h, now = Health(), T0
    consecutive = 0
    for e in events:
        now += timedelta(seconds=1)
        h = step(h, e, now, threshold=3, window_s=60, cooldown_s=30)  # type: ignore[arg-type]
        consecutive = 0 if e == "success" else consecutive + 1
        if e == "success":
            assert not h.is_open(now) and h.failures == 0
        if consecutive and consecutive % 3 == 0:
            assert h.is_open(now)


def test_no_llm_client_import_outside_src_llm() -> None:
    """Model SDKs are imported only by the routing adapters and the legacy LlmPort adapter
    (``platform/llm.py``, migrated behind the router: OI-LLM-2)."""
    allowed = {"src/llm/adapters/anthropic_api.py", "src/account_intelligence/platform/llm.py"}
    pattern = re.compile(r"^\s*(import|from)\s+(anthropic|openai|google\.genai|vertexai)\b", re.M)
    found = {
        str(p.relative_to(ROOT))
        for p in (ROOT / "src").rglob("*.py")
        if "/rag/" not in str(p) and pattern.search(p.read_text())
    }
    assert found <= allowed


@pytest.mark.asyncio
async def test_a_candidate_whose_adapter_is_absent_is_never_rerouted(cfg: RoutingConfig) -> None:
    from llm.errors import AdapterNotConfigured

    store = InMemoryCallStore()
    health = InMemoryHealth(2, 60, 30)
    r = Router(
        cfg,
        {"vertex_ai": ScriptedAdapter(lambda c, s, u: '{"answer": "x"}')},
        store,
        health,
        lambda: T0,
    )
    with pytest.raises(AdapterNotConfigured):
        await r.call("extract_facts", "p", "h", {}, Out, scope())


def test_container_router_factory_builds_a_router_over_one_access_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace as dc_replace
    from types import SimpleNamespace

    from account_intelligence.app import container
    from account_intelligence.app.container import RuntimeMisconfigured, router_factory

    with pytest.raises(RuntimeMisconfigured, match="benchmark"):
        router_factory(object(), "anthropic_api")  # no benchmark: the router cannot go live
    real = load_routing_config()
    monkeypatch.setattr(
        "llm.config.load_routing_config",
        lambda: dc_replace(real, benchmark_report="benchmark-test"),
    )
    make = router_factory(object(), "anthropic_api")
    deps = SimpleNamespace(txn=None, tenant_id=uuid4(), run_id=uuid4())
    assert set(make(deps).adapters) == {"anthropic_api"}
    assert container is not None


def test_routing_changes_are_governed(tmp_path: Path) -> None:
    import shutil
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from check_routing_governance import check

    assert check() == []  # the repository's own config is recorded
    (tmp_path / "config").mkdir()
    (tmp_path / "docs/routing").mkdir(parents=True)
    shutil.copy(ROOT / "config/model_routing.yaml", tmp_path / "config/model_routing.yaml")
    (tmp_path / "docs/routing/DECISIONS.md").write_text("nothing here\n")
    assert check(tmp_path)  # an unrecorded change fails
    import hashlib

    h = hashlib.sha256((tmp_path / "config/model_routing.yaml").read_bytes()).hexdigest()
    (tmp_path / "docs/routing/DECISIONS.md").write_text(f"| {h} | benchmark: benchmark-x |\n")
    assert check(tmp_path) == ["benchmark report benchmark-x does not exist"]
    (tmp_path / "docs/routing/benchmark-x.md").write_text("# report\n")
    assert check(tmp_path) == []
