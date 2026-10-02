"""Boundaries and exact records found by mutation testing (mutmut 3.8 on engine, product,
primary, leads). Each test pins a behaviour a surviving mutant changed."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from account_intelligence.grading.config import GradingConfig
from account_intelligence.grading.engine import (
    AttributeGrade,
    EngineEvidence,
    attribute_class,
    check_tier,
    grade_attribute,
    normaliser_for,
    recency,
    score_value,
)
from account_intelligence.grading.errors import LeadRuleViolation, UnknownAttributeClass, UnknownTier
from account_intelligence.grading.leads import (
    QueryRecord,
    account_terms,
    evaluate_lead,
    mark_incomplete,
    mark_upgraded,
    new_lead,
    repass,
    similarity,
    submit_query,
)
from account_intelligence.grading.primary import VerifiedEvidence, read_in_primary_this_run
from account_intelligence.grading.product import ProductInputs, _rule, product_grade
from tests.grading.conftest import NOW
from tests.grading.world import RUN_INFO, _fetch


def ev(tier: str, group: str, value: str = "x", known: bool = True) -> EngineEvidence:
    return EngineEvidence(tier, group, NOW, "observed", value, known)


# ------------------------------------------------------------------ engine


def test_typed_error_messages_name_the_offending_value(cfg: GradingConfig) -> None:
    with pytest.raises(UnknownTier, match="T9"):
        check_tier("T9")
    with pytest.raises(UnknownAttributeClass, match="weather.today"):
        attribute_class("weather.today", cfg)
    with pytest.raises(ValueError, match="half_life_days must be positive"):
        recency(1, 0)
    assert recency(1, 0.5) == 0.25  # a half-life below one day is valid


def test_unknown_prefix_normaliser_is_opaque(cfg: GradingConfig) -> None:
    assert normaliser_for("weather.today", cfg) == "opaque"
    assert normaliser_for("exec.cto.name", cfg) == "casefold_text"


def test_lineage_unknown_flag_only_when_a_group_is_unknown(cfg: GradingConfig) -> None:
    assert score_value("x", [ev("T1", "a"), ev("T2", "b")], "technology", NOW, cfg).flags == ()
    assert "lineage_unknown" in score_value("x", [ev("T1", "a"), ev("T2", "b", known=False)], "technology", NOW, cfg).flags


def test_no_evidence_and_blocked_results_are_exact(cfg: GradingConfig) -> None:
    assert grade_attribute([], "hiring", NOW, cfg) == AttributeGrade("no_evidence", None, ())
    assert grade_attribute([ev("T1", "a")], "hiring", NOW, cfg, blocked_reason="r") == AttributeGrade(
        "blocked", None, (), ("blocked",), "r"
    )


def test_equal_confidence_prefers_more_independent_groups(cfg: GradingConfig) -> None:
    # T4 weight 0.5 makes two fresh T4 groups exactly 0.75, equal to one fresh T2 group.
    tuned = cfg.model_copy(update={"weights": {**cfg.weights, "T4": 0.5}})
    items = [ev("T2", "g1", "x"), ev("T4", "g2", "y"), ev("T4", "g3", "y")]
    r = grade_attribute(items, "technology", NOW, tuned, normaliser="opaque")
    assert r.status == "graded" and "possible_conflict" in r.flags
    assert r.best is not None and r.best.value == "y" and r.best.confidence == Decimal("0.75")
    assert [s.value for s in r.values] == ["y", "x"]


# ------------------------------------------------------------------ product and primary


def test_input_without_an_engine_grade_falls_back_to_hypothesis_never_lead(cfg: GradingConfig) -> None:
    assert product_grade(ProductInputs("graded", None, False), cfg) == ("hypothesis", "fallback")
    with pytest.raises(ValueError, match="made_up"):
        _rule("made_up", ProductInputs("graded", "A", False), cfg)


def test_fetch_exactly_at_run_start_counts_and_success_has_no_reasons() -> None:
    fetch = replace(_fetch("c1", "scrape_job_page", "full_page_read", "job_board", "p_scrape"), fetched_at=RUN_INFO.started_at)
    e = VerifiedEvidence("u", "T1", "c1", "u", True)
    r = read_in_primary_this_run([e], {"c1": fetch}, RUN_INFO, conflicting=False)
    assert r.value is True and r.reasons == ()
    early = replace(fetch, fetched_at=RUN_INFO.started_at - timedelta(microseconds=1))
    assert read_in_primary_this_run([e], {"c1": early}, RUN_INFO, conflicting=False).reasons == ("fetched_before_run_start",)


# ------------------------------------------------------------------ leads


def test_account_terms_exact() -> None:
    assert account_terms("acme.example") == frozenset({"acme"})
    assert account_terms("www.sub.acme.example", "Acme Corp") == frozenset({"sub", "acme", "corp"})
    assert account_terms("ACME.example", "ＡＣＭＥ") == frozenset({"acme"})


def test_similarity_formula_and_one_empty_set() -> None:
    assert similarity(frozenset({"a", "b"}), frozenset({"a"})) == 0.5
    assert similarity(frozenset({"a"}), frozenset()) == 0.0


def _ledger(*qs: tuple[str, str, str]) -> dict[str, object]:
    return {ref: _fetch(ref, "web_search", "search_snippet", cat, "p_search", queries=(q,)) for ref, cat, q in qs}


def test_similarity_exactly_at_the_threshold_is_a_duplicate(cfg: GradingConfig) -> None:
    f = _ledger(("a", "web_search", "salesforce admin"), ("b", "job_board", "salesforce"))
    s = submit_query(new_lead("k"), "salesforce admin", "web_search", "a", f, frozenset(), cfg)  # type: ignore[arg-type]
    s = submit_query(s, "salesforce", "job_board", "b", f, frozenset(), cfg)  # type: ignore[arg-type]
    assert s.queries[-1].rejection_reason == "near_duplicate"  # Jaccard 0.5 is not below 0.5


def test_query_records_are_exact(cfg: GradingConfig) -> None:
    f = _ledger(("a", "web_search", "salesforce admin"))
    s = submit_query(new_lead("k"), "salesforce admin", "web_search", "a", f, frozenset(), cfg)  # type: ignore[arg-type]
    assert s.queries == (QueryRecord("salesforce admin", "web_search", "a", True, None, ("admin", "salesforce")),)
    for ref, cat, query, reason in [
        ("missing", "web_search", "q one", "call_not_in_ledger"),
        ("a", "job_board", "q two", "category_mismatch"),
        ("a", "web_search", "never sent", "query_not_sent_by_call"),
    ]:
        r = submit_query(new_lead("k"), query, cat, ref, f, frozenset(), cfg)  # type: ignore[arg-type]
        assert r.queries == (QueryRecord(query, cat, ref, False, reason, ()),)
    assert new_lead("key-1").lead_key == "key-1"
    done = replace(new_lead("k"), status="complete")
    with pytest.raises(LeadRuleViolation, match="complete"):
        submit_query(done, "q", "web_search", "a", f, frozenset(), cfg)  # type: ignore[arg-type]


def test_state_transitions_are_exact(cfg: GradingConfig) -> None:
    f = _ledger(("a", "web_search", "q one"))
    up = mark_upgraded(new_lead("k"), "a", f)  # type: ignore[arg-type]
    assert (up.status, up.upgraded_by, up.exit_reason) == ("upgraded", "a", "upgraded")
    with pytest.raises(LeadRuleViolation, match="successful call"):
        mark_upgraded(new_lead("k"), "missing", f)  # type: ignore[arg-type]
    inc = mark_incomplete(new_lead("k"), "budget")
    assert (inc.status, inc.reason, inc.exit_reason) == ("incomplete", "budget", "budget")
    parked = replace(inc, rejected_this_pass=3)
    again = repass(parked, cfg)
    assert (again.status, again.reason, again.exit_reason, again.repasses_used, again.rejected_this_pass) == (
        "pending", None, None, 1, 0)
    with pytest.raises(LeadRuleViolation, match="re-pass budget used"):
        repass(replace(again, status="incomplete"), cfg)
    with pytest.raises(LeadRuleViolation, match="only an incomplete lead"):
        repass(new_lead("k"), cfg)


def test_forged_complete_with_one_category_is_refused(cfg: GradingConfig) -> None:
    three_one_cat = tuple(QueryRecord(q, "web_search", c, True, None, (q,)) for q, c in [("a", "1"), ("b", "2"), ("c", "3")])
    with pytest.raises(LeadRuleViolation, match="required distinct queries"):
        evaluate_lead(replace(new_lead("k"), status="complete", queries=three_one_cat), cfg)
    two_two_cat = (QueryRecord("a", "web_search", "1", True, None, ("a",)), QueryRecord("b", "job_board", "2", True, None, ("b",)))
    with pytest.raises(LeadRuleViolation):
        evaluate_lead(replace(new_lead("k"), status="complete", queries=two_two_cat), cfg)


def test_every_rejection_record_is_exact(cfg: GradingConfig) -> None:
    f = {
        "ok": _fetch("ok", "web_search", "search_snippet", "web_search", "p_search", queries=("salesforce admin",)),
        "dup": _fetch("dup", "web_search", "search_snippet", "job_board", "p_search", queries=("admin salesforce",)),
        "bad": _fetch("bad", "web_search", "search_snippet", "web_search", "p_search", queries=("x y",), success=False),
        "odd": _fetch("odd", "web_search", "search_snippet", "telepathy", "p_search", queries=("x y",)),
        "stop": _fetch("stop", "web_search", "search_snippet", "web_search", "p_search", queries=("the of",)),
        "w2": _fetch("w2", "web_search", "search_snippet", "web_search", "p_search", queries=("crm team opening",)),
        "w3": _fetch("w3", "web_search", "search_snippet", "web_search", "p_search", queries=("cloud partner",)),
    }
    for ref, cat, query, reason in [("bad", "web_search", "x y", "call_failed"), ("odd", "telepathy", "x y", "category_unknown"),
                                    ("stop", "web_search", "the of", "empty_after_normalisation")]:
        r = submit_query(new_lead("k"), query, cat, ref, f, frozenset(), cfg)  # type: ignore[arg-type]
        assert r.queries == (QueryRecord(query, cat, ref, False, reason, ()),)
    s = submit_query(new_lead("k"), "salesforce admin", "web_search", "ok", f, frozenset(), cfg)  # type: ignore[arg-type]
    s = submit_query(s, "admin salesforce", "job_board", "dup", f, frozenset(), cfg)  # type: ignore[arg-type]
    assert s.queries[-1] == QueryRecord("admin salesforce", "job_board", "dup", False, "near_duplicate", ())
    s = submit_query(s, "crm team opening", "web_search", "w2", f, frozenset(), cfg)  # type: ignore[arg-type]
    s = submit_query(s, "cloud partner", "web_search", "w3", f, frozenset(), cfg)  # type: ignore[arg-type]
    assert s.queries[-1] == QueryRecord("cloud partner", "web_search", "w3", False, "needs_new_category", ())


def test_error_messages_are_exact(cfg: GradingConfig) -> None:
    from datetime import datetime

    from account_intelligence.grading.errors import NaiveDatetime
    from account_intelligence.grading.engine import group_strengths

    with pytest.raises(NaiveDatetime, match="^now must be a timezone-aware datetime$"):
        group_strengths([], "hiring", datetime(2026, 1, 1), cfg)
    with pytest.raises(NaiveDatetime, match="^now must be a timezone-aware datetime$"):
        grade_attribute([], "hiring", datetime(2026, 1, 1), cfg)
    with pytest.raises(NaiveDatetime, match="^evidence_date must be a timezone-aware datetime$"):
        group_strengths([EngineEvidence("T1", "g", datetime(2026, 1, 1), "observed", "x")], "hiring", NOW, cfg)
    with pytest.raises(UnknownAttributeClass, match="^unknown attribute class 'weather'$"):
        group_strengths([], "weather", NOW, cfg)
    with pytest.raises(ValueError, match="^half_life_days must be positive$"):
        recency(1, 0)
    f = {"a": _fetch("a", "web_search", "search_snippet", "web_search", "p_search", queries=("q",))}
    with pytest.raises(LeadRuleViolation, match="^an upgrade needs a successful call in this run's ledger$"):
        mark_upgraded(new_lead("k"), "nope", f)  # type: ignore[arg-type]
    with pytest.raises(LeadRuleViolation, match="^only an incomplete lead can be re-passed$"):
        repass(new_lead("k"), cfg)
    with pytest.raises(LeadRuleViolation, match="^re-pass budget used$"):
        repass(replace(new_lead("k"), status="incomplete", repasses_used=1), cfg)
    with pytest.raises(LeadRuleViolation, match="^lead marked complete without the required distinct queries$"):
        evaluate_lead(replace(new_lead("k"), status="complete"), cfg)


def test_default_normaliser_is_opaque_so_no_automatic_conflict(cfg: GradingConfig) -> None:
    r = grade_attribute([ev("T1", "a", "x"), ev("T2", "b", "y")], "technology", NOW, cfg)
    assert r.status == "graded" and r.flags == ("possible_conflict",)


def test_equal_strength_in_one_group_records_the_better_tier(cfg: GradingConfig) -> None:
    from account_intelligence.grading.engine import group_strengths

    tuned = cfg.model_copy(update={"weights": {**cfg.weights, "T2": cfg.weights["T1"]}})
    for order in ([ev("T1", "g"), ev("T2", "g")], [ev("T2", "g"), ev("T1", "g")]):
        assert group_strengths(order, "technology", NOW, tuned)[0].tier == "T1"
