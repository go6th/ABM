"""WS5 pure rules (AL2 to AL4, AL6, AL7): the registry, subscribe-up, caps, freshness and
templates, without a database (so the mutation check exercises them)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from account_intelligence.safety.client_output import ClientOutputLeak, scan_text
from alerts import caps, freshness, rules, subscriptions, templates
from tests.phase4.conftest import NOW

APP = "https://app.test"


def test_the_rule_registry_is_the_sourced_ten_plus_marked_proposals() -> None:
    reg = rules.load()
    assert {r for r, v in reg.items() if not v.proposal} == set(rules.SOURCED_IDS)
    assert {r for r, v in reg.items() if v.proposal} == {
        "facility_expansion",
        "vendor_customer_story",
    }
    assert reg["confidence_drop"].tier == "digest" and reg["funding_ma"].grade_floor == "probable"
    assert reg["new_tech_detection"].grade_floor == "hypothesis"  # "ignore below grade C"
    asset = {"rules": [{"id": "new_build_role", "tier": "immediate", "delivery": ["email"]}]}
    extra = {
        "extra_rules": [],
        "rule_signals": {"new_build_role": {"signals": ["x"], "grade_floor": "A+"}},
    }
    with pytest.raises(rules.RulesInvalid):
        rules.build(asset, extra)  # a grade the engine does not define
    with pytest.raises(rules.RulesInvalid):
        rules.build(
            {"rules": [{"id": "new_build_role", "tier": "immediate", "delivery": []}]},
            {
                "extra_rules": [],
                "rule_signals": {"new_build_role": {"signals": ["x"], "grade_floor": "probable"}},
            },
        )  # not the ten
    with pytest.raises(rules.RulesInvalid):
        rules.build(
            asset,
            {"extra_rules": [{"id": "x", "tier": "digest", "delivery": []}], "rule_signals": {}},
        )  # an added rule must be a marked proposal


def test_new_users_get_the_digest_only_and_subscribe_up() -> None:
    reg = rules.load()
    new = subscriptions.Subscription()
    assert subscriptions.channels(new, reg["new_build_role"], account="a", score=50, now_ts=0) == (
        ["in_app", "digest"],
        None,
    )
    up = subscriptions.Subscription(immediate_rules=frozenset({"new_build_role"}), slack=True)
    assert subscriptions.channels(up, reg["new_build_role"], account="a", score=50, now_ts=0) == (
        ["in_app", "email", "slack"],
        None,
    )
    assert subscriptions.channels(up, reg["confidence_drop"], account="a", score=50, now_ts=0)[
        0
    ] == ["in_app", "digest"]  # never real time
    for sub, why in (
        (subscriptions.Subscription(muted_accounts=frozenset({"a"})), "muted"),
        (subscriptions.Subscription(snoozed_until={"a": 10.0}), "snoozed"),
        (subscriptions.Subscription(threshold=99), "below_threshold"),
        (subscriptions.Subscription(digest=False), "not_subscribed"),
    ):
        assert subscriptions.channels(
            sub, reg["new_build_role"], account="a", score=50, now_ts=0
        ) == ([], why)


def test_caps_suppress_or_batch_with_a_reason() -> None:
    c = caps.Caps(1, 3, 10, 5)
    zero = caps.Sent(0, 0, 0, 0)
    assert caps.decide("new_build_role", "immediate", zero, c) == ("send", None)
    assert caps.decide("confidence_drop", "digest", zero, c) == ("batch", "digest_rule")
    assert caps.decide("tier_one_news", "immediate", caps.Sent(1, 0, 0, 0), c) == (
        "suppress",
        "account_news_cap",
    )
    assert caps.decide("x", "immediate", caps.Sent(0, 3, 0, 0), c) == ("batch", "user_hourly_cap")
    assert caps.decide("x", "immediate", caps.Sent(0, 0, 10, 0), c) == ("batch", "user_daily_cap")
    assert caps.decide("x", "immediate", caps.Sent(0, 0, 0, 5), c) == ("batch", "digest_threshold")


def test_the_freshness_sli_counts_misses_and_pending() -> None:
    d = NOW
    fr = freshness.compute(
        [
            (d, d + timedelta(hours=2)),
            (d, d + timedelta(hours=30)),
            (d - timedelta(hours=30), None),
            (d, None),
        ],
        d + timedelta(hours=1),
    )
    assert (fr.good, fr.judged, fr.pending) == (1, 3, 1) and fr.meets_slo is False
    assert freshness.compute([], d).ratio is None and freshness.compute([], d).meets_slo is None


def test_every_template_is_lint_clean_and_client_safe() -> None:
    from alerts.templates import _SUBJECT

    for rule_id in _SUBJECT:
        m = templates.render(
            rule_id,
            account="acme.example",
            summary="A finding.",
            grade="probable",
            source_code="S-0001",
            event_type="expansion",
            is_lead=False,
            app_link=f"{APP}/alerts/x",
        )
        assert not scan_text(m.subject + m.body)
    from account_intelligence.connectors.registry_internal import vendor_names
    from account_intelligence.connectors.settings import load_providers_config

    vendor = sorted(vendor_names(load_providers_config()).values())[0]
    with pytest.raises(ClientOutputLeak):
        templates.render(
            "tier_one_news",
            account="acme.example",
            summary=f"Data from {vendor}",
            grade="probable",
            source_code=None,
            event_type="expansion",
            is_lead=False,
            app_link=APP,
        )
