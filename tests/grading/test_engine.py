"""Engine: reference parity, fixes F1 to F5, properties and exact boundaries."""

from __future__ import annotations

import itertools
import random
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from account_intelligence.grading.config import GradingConfig
from account_intelligence.grading.engine import (
    EngineEvidence,
    attribute_class,
    confidence,
    engine_grade,
    grade_attribute,
    group_strengths,
    recency,
)
from account_intelligence.grading.errors import NaiveDatetime, UnknownAttributeClass, UnknownTier
from tests.grading.conftest import NOW, SKILL, load_reference

REF = load_reference()


def ev(
    tier: str,
    group: str,
    value: str = "x",
    age: float = 0.0,
    kind: str = "observed",
    known: bool = True,
) -> EngineEvidence:
    return EngineEvidence(tier, group, NOW - timedelta(days=age), kind, value, known)  # type: ignore[arg-type]


def g(
    cfg: GradingConfig,
    items: list[EngineEvidence],
    cls: str = "leadership",
    norm: str = "casefold_text",
):  # type: ignore[no-untyped-def]
    return grade_attribute(items, cls, NOW, cfg, normaliser=norm)  # type: ignore[arg-type]


# ------------------------------------------------------------------ config equals the asset


def test_loaded_config_equals_the_skill_asset(cfg: GradingConfig) -> None:
    asset = yaml.safe_load((SKILL / "assets" / "grading.yaml").read_text())
    eng = asset["engine"]
    assert cfg.weights == {k.split("_")[0]: v["weight"] for k, v in eng["source_tiers"].items()}
    assert cfg.half_lives == {k: float(v) for k, v in eng["half_life_days"].items()}
    lead = asset["product_grades"]["lead"]["stays_lead_only_if"]
    assert (cfg.lead_rule.min_distinct_queries, cfg.lead_rule.min_distinct_tool_categories) == (
        lead["min_distinct_queries"],
        lead["min_distinct_tool_categories"],
    )
    assert cfg.eval_gates.model_dump() == eng["eval_gates"]
    t = cfg.thresholds
    assert (t.a, t.b, t.c, t.d_below, t.conflict_above) == tuple(
        Decimal(x) for x in ("0.85", "0.70", "0.50", "0.50", "0.50")
    )
    assert (
        f"c >= {t.a} and >= {t.a_min_groups} independent lineage groups, one of them"
        f" {t.a_required_tiers[0]} or {t.a_required_tiers[1]}" == eng["grades"]["A"]["rule"]
    )
    assert {
        k: v["may_state_to_prospect"] for k, v in asset["product_grades"].items()
    } == cfg.may_state


def test_reference_selftest_runs_green() -> None:
    out = subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "grading_reference.py"), "--selftest"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0 and "selftest OK" in out.stdout


# ------------------------------------------------------------------ worked examples


def test_worked_examples(cfg: GradingConfig) -> None:
    r = g(cfg, [ev("T1", "site", "jane"), ev("T2", "curated", "jane")])
    assert (r.best.confidence, r.best.engine_grade) == (Decimal("0.975000000"), "A")  # type: ignore[union-attr]
    r = g(cfg, [ev("T4", "up"), ev("T4", "up")])
    assert (r.best.confidence, r.best.engine_grade) == (Decimal("0.4"), "D")  # type: ignore[union-attr]
    r = g(cfg, [ev("T1", "site")], "funding")
    assert (r.best.confidence, r.best.engine_grade) == (Decimal("0.9"), "B")  # type: ignore[union-attr]
    r = g(cfg, [ev("T3", "n1"), ev("T3", "n2")], "funding")
    assert (r.best.confidence, r.best.engine_grade) == (Decimal("0.91"), "B")  # type: ignore[union-attr]
    r = g(cfg, [ev("T1", "site", age=21)], "hiring")
    assert r.best.confidence == Decimal("0.45")  # type: ignore[union-attr]
    assert g(cfg, [ev("T1", "site", "jane"), ev("T2", "cur", "john")]).status == "conflicting"
    r = g(cfg, [ev("T1", "site", "jane"), ev("T4", "agg", "john")])
    assert (r.status, r.best.value) == ("graded", "jane")  # type: ignore[union-attr]
    assert g(cfg, [ev("T2", "g"), ev("T4", "g")], "technology").best.confidence == Decimal("0.75")  # type: ignore[union-attr]
    assert g(cfg, []).status == "no_evidence"


@settings(max_examples=300, deadline=None)
@given(
    items=st.lists(
        st.tuples(
            st.sampled_from(["T1", "T2", "T3", "T4"]),
            st.sampled_from(["g1", "g2", "g3"]),
            st.sampled_from(["x", "y"]),
            st.integers(0, 800),
        ),
        min_size=1,
        max_size=6,
    ),
    cls=st.sampled_from(["hiring", "technology", "leadership", "funding"]),
)
def test_random_parity_with_the_reference(cfg: GradingConfig, items, cls: str) -> None:  # type: ignore[no-untyped-def]
    """Agrees with the reference except the listed intentional differences (F1, F4)."""
    ref = REF.grade_attribute(
        [REF.Evidence(t, gr, NOW - timedelta(days=a), v) for t, gr, v, a in items], cls, NOW
    )
    ours = g(cfg, [ev(t, gr, v, a) for t, gr, v, a in items], cls)
    if ref["status"] == "conflicting":
        assert ours.status == "conflicting"
        assert {s.value for s in ours.values} == set(ref["values"])
        return
    assert ours.status == "graded" and ours.best is not None
    ref_scores = {}
    for v in {x[2] for x in items}:
        r1 = REF.grade_attribute(
            [REF.Evidence(t, gr, NOW - timedelta(days=a), vv) for t, gr, vv, a in items if vv == v],
            cls,
            NOW,
        )
        ref_scores[v] = r1
    top = max(r["confidence"] for r in ref_scores.values())
    tied = [v for v, r in ref_scores.items() if r["confidence"] == top]
    assert ours.best.value in tied  # F1: the reference picks among ties by input order
    ref_items = [
        REF.Evidence(t, gr, NOW - timedelta(days=a), v)
        for t, gr, v, a in items
        if v == ours.best.value
    ]
    raw = REF.confidence(REF.group_strengths(ref_items, cls, NOW))  # unrounded: no double rounding
    assert abs(float(ours.best.confidence) - raw) <= 1e-9
    if (
        abs(float(ours.best.confidence) - 0.85) > 1e-6
        and abs(float(ours.best.confidence) - 0.70) > 1e-6
    ):
        assert (
            ours.best.engine_grade == ref_scores[ours.best.value]["grade"]
        )  # F4 differs only on exact boundaries


# ------------------------------------------------------------------ F1 to F5
# (each was shown failing against the reference first)


def test_f1_reference_tie_break_depends_on_input_order(cfg: GradingConfig) -> None:
    a, b = (
        REF.Evidence("T2", "g1", NOW, "x"),
        REF.Evidence("T2", "g1", NOW, "y"),
    )  # one group: no conflict
    assert (
        REF.grade_attribute([a, b], "technology", NOW)["value"]
        != REF.grade_attribute([b, a], "technology", NOW)["value"]
    )
    one = g(cfg, [ev("T2", "g1", "x"), ev("T2", "g1", "y")], "technology")
    two = g(cfg, [ev("T2", "g1", "y"), ev("T2", "g1", "x")], "technology")
    assert one == two and one.best.value == "x"  # type: ignore[union-attr]


def test_f2_reference_raises_untyped_errors_and_ours_are_typed(cfg: GradingConfig) -> None:
    with pytest.raises(KeyError):
        REF.grade_attribute([REF.Evidence("T9", "g", NOW, "x")], "hiring", NOW)
    with pytest.raises(KeyError):
        REF.grade_attribute([REF.Evidence("T1", "g", NOW, "x")], "weather", NOW)
    with pytest.raises(TypeError):
        REF.grade_attribute([REF.Evidence("T1", "g", datetime(2026, 1, 1), "x")], "hiring", NOW)
    with pytest.raises(UnknownTier):
        g(cfg, [ev("T9", "g")])
    with pytest.raises(UnknownAttributeClass):
        g(cfg, [ev("T1", "g")], "weather")
    with pytest.raises(UnknownAttributeClass):
        attribute_class("weather.today", cfg)
    with pytest.raises(NaiveDatetime):
        grade_attribute(
            [EngineEvidence("T1", "g", datetime(2026, 1, 1), "observed", "x")], "hiring", NOW, cfg
        )
    with pytest.raises(NaiveDatetime):
        grade_attribute([ev("T1", "g")], "hiring", datetime(2026, 1, 1), cfg)


def test_f3_reference_scores_an_old_article_as_fresh_and_ours_ages_it(cfg: GradingConfig) -> None:
    ref = REF.grade_attribute(
        [REF.Evidence("T3", "news", NOW, "x")], "funding", NOW
    )  # retrieved today
    assert (ref["confidence"], ref["grade"]) == (0.7, "B")
    published = NOW - timedelta(days=3 * 365)
    ours = g(cfg, [EngineEvidence("T3", "news", published, "published", "x")], "funding")
    assert ours.best.engine_grade == "D" and ours.best.confidence < Decimal("0.1")  # type: ignore[union-attr]


def test_f3_unknown_date_cannot_support_grade_a(cfg: GradingConfig) -> None:
    known = g(cfg, [ev("T1", "site"), ev("T2", "cur")])
    assert known.best.engine_grade == "A"  # type: ignore[union-attr]
    capped = g(cfg, [ev("T1", "site"), ev("T2", "cur", kind="unknown")])
    assert capped.best.engine_grade == "B" and "date_unknown" in capped.best.flags  # type: ignore[union-attr]


def test_d11_unknown_lineage_reaching_a_is_capped_and_flagged(cfg: GradingConfig) -> None:
    r = g(cfg, [ev("T1", "site"), ev("T2", "provider:p_org", known=False)])
    assert r.best.engine_grade == "B" and {"needs_lineage_review", "lineage_unknown"} <= set(
        r.best.flags
    )  # type: ignore[union-attr]
    r = g(cfg, [ev("T1", "site"), ev("T2", "cur"), ev("T4", "unk", known=False)])
    assert r.best.engine_grade == "A" and "lineage_unknown" in r.best.flags  # type: ignore[union-attr]


def test_f4_exact_boundaries(cfg: GradingConfig) -> None:
    # fresh T2 + fresh T4 = 1 - 0.25 * 0.60 = 0.85 exactly: grade A
    r = g(cfg, [ev("T2", "a"), ev("T4", "b")])
    assert r.best.confidence == Decimal("0.85") and r.best.engine_grade == "A"  # type: ignore[union-attr]
    raw = 1 - (1 - 0.75) * (1 - 0.40)
    assert REF.grade(raw, {"a": (0.75, "T2"), "b": (0.4, "T4")}) == (
        "A" if raw >= 0.85 else "B"
    )  # reference depends on float error
    hl = cfg.half_lives["technology"]
    # one T1 source aged so that c = 0.70 exactly: 0.9 * 2^(-a/h) = 0.7
    import math

    age = -math.log2(0.7 / 0.9) * hl
    r = g(cfg, [ev("T1", "site", age=age)], "technology")
    assert r.best.confidence == Decimal("0.7") and r.best.engine_grade == "B"  # type: ignore[union-attr]
    age = -math.log2(0.5 / 0.9) * hl
    r = g(cfg, [ev("T1", "site", age=age)], "technology")
    assert r.best.confidence == Decimal("0.5") and r.best.engine_grade == "C"  # type: ignore[union-attr]
    # conflicting requires both strictly above 0.50
    r = g(cfg, [ev("T1", "a", "x", age=age), ev("T1", "b", "y", age=age)], "technology")
    assert r.status == "graded"


def test_f5_now_is_required(cfg: GradingConfig) -> None:
    with pytest.raises(TypeError):
        grade_attribute([ev("T1", "g")], "hiring", cfg=cfg)  # type: ignore[call-arg]


def test_one_half_life_and_timezones(cfg: GradingConfig) -> None:
    assert recency(21, 21) == 0.5 and recency(-3, 21) == 1.0
    with pytest.raises(ValueError):
        recency(1, 0)
    ist = timezone(timedelta(hours=5, minutes=30))
    same = EngineEvidence("T1", "s", NOW.astimezone(ist), "observed", "x")
    assert group_strengths([same], "hiring", NOW, cfg)[0].strength == 0.9


def test_blocked_and_opaque_values(cfg: GradingConfig) -> None:
    b = grade_attribute(
        [ev("T1", "s")],
        "hiring",
        NOW,
        cfg,
        blocked_reason="p_signals.news_events: provider temporarily unavailable",
    )
    assert b.status == "blocked" and b.best is None and b.blocked_reason
    r = g(cfg, [ev("T1", "a", "x"), ev("T2", "b", "y")], norm="opaque")
    assert r.status == "graded" and "possible_conflict" in r.flags


# ------------------------------------------------------------------ properties

TIER = st.sampled_from(["T1", "T2", "T3", "T4"])
ITEM = st.tuples(
    TIER,
    st.sampled_from(["g1", "g2", "g3", "g4"]),
    st.floats(0, 1000),
    st.sampled_from(["observed", "published", "unknown"]),
)


@settings(max_examples=200, deadline=None)
@given(st.lists(ITEM, min_size=1, max_size=8), st.randoms())
def test_property_order_independence_and_unit_interval(
    cfg: GradingConfig, items, rnd: random.Random
) -> None:  # type: ignore[no-untyped-def]
    evs = [
        ev(t, gr, "v" + gr[-1:] if i % 2 else "x", a, k) for i, (t, gr, a, k) in enumerate(items)
    ]
    shuffled = evs[:]
    rnd.shuffle(shuffled)
    a, b = g(cfg, evs, "technology"), g(cfg, shuffled, "technology")
    assert a == b
    for s in a.values:
        assert Decimal(0) <= s.confidence <= Decimal(1)


@settings(max_examples=200, deadline=None)
@given(st.lists(ITEM, min_size=1, max_size=6), TIER, st.floats(0, 1000))
def test_property_independent_source_never_lowers_and_same_group_never_exceeds_max(
    cfg: GradingConfig, items, tier: str, age: float
) -> None:  # type: ignore[no-untyped-def]
    evs = [ev(t, gr, "x", a, k) for t, gr, a, k in items]
    base = group_strengths(evs, "technology", NOW, cfg)
    more = group_strengths([*evs, ev(tier, "new-independent", "x", age)], "technology", NOW, cfg)
    assert confidence(more) >= confidence(base)
    same = group_strengths([*evs, ev(tier, "g1", "x", age)], "technology", NOW, cfg)
    g1 = [x for x in same if x.group == "g1"][0]
    expected = max(
        [x.strength for x in base if x.group == "g1"]
        + [cfg.weights[tier] * recency(age, cfg.half_lives["technology"])]
    )
    assert g1.strength == pytest.approx(expected, abs=1e-12)  # timedelta keeps microseconds only


@settings(max_examples=200, deadline=None)
@given(st.lists(ITEM, min_size=1, max_size=6))
def test_property_a_only_with_two_groups_and_a_t1_or_t2_group(cfg: GradingConfig, items) -> None:  # type: ignore[no-untyped-def]
    groups = group_strengths(
        [ev(t, gr, "x", a, k) for t, gr, a, k in items], "technology", NOW, cfg
    )
    grade, _ = engine_grade(groups, cfg)
    if grade == "A":
        assert len(groups) >= 2 and any(x.tier in ("T1", "T2") for x in groups)
        assert (
            all(x.date_known for x in groups)
            or engine_grade([x for x in groups if x.date_known], cfg)[0] == "A"
        )


@settings(max_examples=100, deadline=None)
@given(st.floats(0, 3000), st.floats(0, 3000))
def test_property_decay_monotone_in_age(a: float, b: float) -> None:
    lo, hi = sorted((a, b))
    assert recency(hi, 90) <= recency(lo, 90)


def test_property_grade_monotone_in_confidence(cfg: GradingConfig) -> None:
    order = {"D": 0, "C": 1, "B": 2, "A": 3}
    ages = [0, 10, 30, 60, 90, 180, 400, 900]
    for a1, a2 in itertools.product(ages, ages):
        groups = group_strengths(
            [ev("T1", "a", age=a1), ev("T2", "b", age=a2)], "technology", NOW, cfg
        )
        older = group_strengths(
            [ev("T1", "a", age=a1 + 50), ev("T2", "b", age=a2 + 50)], "technology", NOW, cfg
        )
        assert order[engine_grade(older, cfg)[0]] <= order[engine_grade(groups, cfg)[0]]


def test_aware_utc_now_from_other_zone_is_accepted(cfg: GradingConfig) -> None:
    other = NOW.astimezone(timezone(timedelta(hours=-7)))
    assert (
        g(cfg, [ev("T1", "s")]).best
        == grade_attribute(
            [ev("T1", "s")], "leadership", other, cfg, normaliser="casefold_text"
        ).best
    )
    assert datetime.now(UTC)  # (no wall clock is read by the engine; this line documents intent)
