"""Model routing behind config/models.yaml and the offline fake provider."""

from __future__ import annotations

import json

import pytest

from app.llm import FakeBehaviour, FakeProvider, LiveProviderUnavailable, ModelRouter, cost_usd
from app.llm.fake import SOURCE_PACK_MARKER
from app.rules import RuleBundle
from app.rules.schemas import ModelProfile

PACK = [
    {"url": "https://a.example/1", "primary": True, "row_ids": ["P2.01"], "text": "One. Two."},
    {"url": "https://a.example/2", "primary": False, "row_ids": ["P2.07"], "text": "Three."},
]
PROMPT = f"extract\n{SOURCE_PACK_MARKER}{json.dumps(PACK)}"


def test_arms_route_tasks_to_profiles(rules: RuleBundle) -> None:
    single = ModelRouter(rules.models, {}, arm="single")
    split = ModelRouter(rules.models, {}, arm="split")
    assert single.profile_for("extraction")[0] == "strong"
    assert split.profile_for("extraction")[0] == "fast"
    assert split.profile_for("narrative")[0] == "strong"
    assert ModelRouter(rules.models, {}).arm_name == rules.models.active_arm


def test_unknown_arm_and_task(rules: RuleBundle) -> None:
    with pytest.raises(KeyError):
        ModelRouter(rules.models, {}, arm="nope")
    with pytest.raises(KeyError):
        ModelRouter(rules.models, {}).profile_for("dreaming")


def test_live_provider_is_not_available_offline(rules: RuleBundle) -> None:
    router = ModelRouter(rules.models, {"fake": FakeProvider()})
    with pytest.raises(LiveProviderUnavailable):
        router.complete("extraction", "sys", PROMPT)


def test_router_records_usage(rules: RuleBundle) -> None:
    fake = FakeProvider()
    router = ModelRouter(rules.models, {"anthropic": fake}, arm="split")
    router.complete("extraction", "sys", PROMPT)
    (usage,) = router.usage
    assert usage.profile == "fast"
    assert usage.model == rules.models.profiles["fast"].model
    assert usage.input_tokens > 0
    assert usage.cost_usd is None  # price not configured, so no invented cost
    assert fake.requests[0].task == "extraction"


def test_cost_from_configured_prices() -> None:
    priced = ModelProfile(
        provider="fake", model="m", input_usd_per_mtok=2.0, output_usd_per_mtok=10.0
    )
    assert cost_usd(priced, 1_000_000, 100_000) == pytest.approx(3.0)
    assert cost_usd(ModelProfile(provider="fake", model="m"), 10, 10) is None


def test_fake_is_deterministic_and_quotes_sources() -> None:
    fake = FakeProvider()
    from app.llm.provider import CompletionRequest

    req = CompletionRequest(task="extraction", model="m", system="s", prompt=PROMPT)
    out1, out2 = fake.complete(req), fake.complete(req)
    assert out1 == out2
    facts = json.loads(out1.text)["facts"]
    assert [f["supporting_sentence"] for f in facts] == ["One.", "Two.", "Three."]
    assert [f["grade"] for f in facts] == ["Confirmed", "Confirmed", "Lead"]


def test_fake_behaviour_knobs() -> None:
    from app.llm.provider import CompletionRequest

    req = CompletionRequest(task="extraction", model="m", system="s", prompt=PROMPT)
    none = FakeProvider({"m": FakeBehaviour(recall=0.0)}).complete(req)
    assert json.loads(none.text)["facts"] == []
    wrong = FakeProvider({"m": FakeBehaviour(miscite_rate=1.0)}).complete(req)
    facts = json.loads(wrong.text)["facts"]
    assert all(
        f["url"] != src["url"] for f, src in zip(facts, [PACK[0], PACK[0], PACK[1]], strict=True)
    )


def test_fake_without_source_pack_returns_nothing() -> None:
    from app.llm.provider import CompletionRequest

    out = FakeProvider().complete(
        CompletionRequest(task="narrative", model="m", system="s", prompt="no pack")
    )
    assert json.loads(out.text) == {"facts": []}
