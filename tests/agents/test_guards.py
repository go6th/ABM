"""Guard decision functions G1 to G13: each with a positive and a lying scenario."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agents.errors import GuardViolation
from agents.guards import (
    consent_decision,
    coverage_verdict,
    evidence_problem,
    facts_digest,
    for_audience,
    internal_history_allowed,
    lead_check,
    lead_drop_decision,
    phrasing_key,
    quoted,
    renderable,
    require_sense_check,
    restates_grade,
    routing_violations,
    spend_route,
)
from agents.manifest import QueryRecord
from agents.ports import ConsentRecord, InternalHistorySettings, RowOutcome, SenseCheckRecord
from agents.schemas import EvidenceRef, PayloadView, StoredFact
from spend.gate import RunCapState

NOW = datetime(2026, 10, 8, tzinfo=UTC)


# G1 --------------------------------------------------------------------------------------------


def test_g1_every_row_needs_a_terminal_status_with_proof() -> None:
    rows = {
        "a": RowOutcome("done", None, 3),
        "b": RowOutcome("blocked", "provider_down", 0),
        "c": RowOutcome("none", "searched_nothing_found", 3),
        "d": RowOutcome("partial", "pull_not_approved", 2),
        "e": RowOutcome("not_done", "no_calls", 0),
    }
    assert coverage_verdict(rows, rows).deliverable


@pytest.mark.parametrize(
    ("outcome", "where"),
    [
        (None, "missing"),
        (RowOutcome(None), "missing"),
        (RowOutcome("in_progress", None, 1), "missing"),
        (RowOutcome("done", None, 0), "unproven"),  # Done without queries
        (RowOutcome("blocked", None, 0), "unproven"),  # Blocked without a reason
        (RowOutcome("partial", "x", 0), "unproven"),  # Partial without queries
        (RowOutcome("none", None, 1), "unproven"),
    ],
)
def test_g1_lying_rows_block_delivery(outcome: RowOutcome | None, where: str) -> None:
    rows = {"ok": RowOutcome("done", None, 1)}
    if outcome is not None:
        rows["bad"] = outcome
    verdict = coverage_verdict(["ok", "bad"], rows)
    assert not verdict.deliverable and getattr(verdict, where) == ("bad",)


# G2 --------------------------------------------------------------------------------------------


def test_g2_unread_match_stays_lead() -> None:
    lead_check("keyword_match", False, "lead")
    lead_check("date_range", False, "lead")
    lead_check("keyword_match", True, "confirmed")  # read this run: the engine may promote
    lead_check("read", True, "confirmed")


@pytest.mark.parametrize("grade", ["confirmed", "probable", "hypothesis"])
def test_g2_engine_promoting_an_unread_match_is_refused(grade: str) -> None:
    with pytest.raises(GuardViolation) as error:
        lead_check("keyword_match", False, grade)
    assert (error.value.guard, error.value.code) == ("G2", "unread_match_promoted")


# G3 --------------------------------------------------------------------------------------------


def q(text: str, tool: str = "web_search", n: int = 0) -> QueryRecord:
    return QueryRecord(text=text, tool_category=tool, result_count=n)


def test_g3_three_phrasings_two_tool_categories() -> None:
    attempts = [
        q("acme mulesoft rollout"),
        q("acme integration platform", "news_feed"),
        q("acme api program 2026"),
    ]
    decision = lead_drop_decision(attempts, 3, 2)
    assert decision.allowed and (decision.phrasings, decision.tool_categories) == (3, 2)


def test_g3_reworded_duplicates_do_not_count() -> None:
    attempts = [
        q("Acme MuleSoft rollout"),
        q("rollout, acme mulesoft!", "news_feed"),
        q("MULESOFT acme rollout"),
    ]
    assert phrasing_key(attempts[0].text) == phrasing_key(attempts[1].text)
    decision = lead_drop_decision(attempts, 3, 2)
    assert not decision.allowed and decision.reason == "too_few_phrasings"


def test_g3_fewer_than_three_phrasings_keeps_the_lead() -> None:
    assert lead_drop_decision([q("a b"), q("c d", "x")], 3, 2).reason == "too_few_phrasings"
    assert lead_drop_decision([], 3, 2).reason == "too_few_phrasings"
    one_tool = [q("a"), q("b"), q("c")]
    assert lead_drop_decision(one_tool, 3, 2).reason == "too_few_tool_categories"


# G4 --------------------------------------------------------------------------------------------

PAYLOAD = PayloadView(
    payload_ref="pl/1",
    row_key="company_news",
    url="https://x.example/n",
    text="Acme opened a new\n  integration centre in Pune.",
)


def test_g4_evidence_must_be_quoted_from_a_given_payload() -> None:
    good = (EvidenceRef(payload_ref="pl/1", quote="acme opened a new integration centre"),)
    assert evidence_problem(good, {"pl/1": PAYLOAD}) is None


@pytest.mark.parametrize(
    ("evidence", "payload", "problem"),
    [
        ((), PAYLOAD, "no_evidence"),
        ((EvidenceRef(payload_ref="pl/9", quote="Acme opened a new"),), PAYLOAD, "unknown_payload"),
        (
            (EvidenceRef(payload_ref="pl/1", quote="Acme closed its Pune office"),),
            PAYLOAD,
            "quote_not_in_payload",
        ),
        (
            (EvidenceRef(payload_ref="pl/1", quote="Acme opened a new"),),
            PAYLOAD.model_copy(update={"url": "file:///etc/passwd"}),
            "no_link",
        ),
    ],
)
def test_g4_fabricated_evidence_is_refused(evidence, payload, problem) -> None:  # type: ignore[no-untyped-def]
    assert evidence_problem(evidence, {"pl/1": payload}) == problem


def test_quoted_is_verbatim_and_never_empty() -> None:
    assert quoted("Chief Information Officer", ["Avery, chief information  officer at X"])
    assert not quoted("Chief Executive Officer", ["Avery, chief information officer"])
    assert not quoted("   ", ["anything"])


# G6 --------------------------------------------------------------------------------------------


def fact(fid: str, grade: str = "lead", vis: str = "public") -> StoredFact:
    return StoredFact(
        fact_id=fid, row_key="company_news", attribute="a", value="v", grade=grade, visibility=vis
    )  # type: ignore[arg-type]


def test_g6_build_needs_a_matching_sense_check() -> None:
    digest = facts_digest([fact("1"), fact("2")])
    assert digest == facts_digest([fact("2"), fact("1")])
    record = SenseCheckRecord(uuid4(), "m" * 64, digest, 0, 0)
    require_sense_check(record, "m" * 64, digest)
    with pytest.raises(GuardViolation, match="sense_check_missing"):
        require_sense_check(None, "m" * 64, digest)
    with pytest.raises(GuardViolation, match="sense_check_stale"):
        require_sense_check(record, "n" * 64, digest)
    with pytest.raises(GuardViolation, match="sense_check_stale"):
        require_sense_check(record, "m" * 64, facts_digest([fact("1", "conflicting")]))


# G7 --------------------------------------------------------------------------------------------


def cap(budget: int | None, spent: int = 0) -> RunCapState:
    return RunCapState(True, 1 if budget is not None else None, budget, spent, 0, {})


def test_g7_unapproved_spend_pauses_or_is_recorded() -> None:
    assert spend_route(cap(100), "scrape", 50, True, frozenset(), True) == ("run", "allowed")
    assert spend_route(cap(None), "scrape", 50, True, frozenset(), True) == ("pause", "no_cap")
    assert spend_route(cap(None), "scrape", 50, True, frozenset(), False) == ("record", "no_cap")
    assert spend_route(cap(100, 90), "scrape", 50, True, frozenset(), True)[0] == "pause"
    assert spend_route(cap(100), "scrape", 50, False, frozenset(), False) == (
        "record",
        "unknown_cost",
    )
    assert spend_route(cap(None), "web", 0, True, frozenset({"web"}), False) == ("run", "free")


# G8 --------------------------------------------------------------------------------------------


def consent(
    version: str = "v1",
    scopes: frozenset[str] = frozenset({"people_data"}),
    revoked: datetime | None = None,
) -> ConsentRecord:
    return ConsentRecord(uuid4(), "admin", NOW, version, scopes, revoked)


def test_g8_no_consent_no_people_pull() -> None:
    assert consent_decision(consent(), "people_data", "v1") is None
    assert consent_decision(None, "people_data", "v1") == "no_consent_record"
    assert consent_decision(consent(), "people_data", None) == "consent_text_not_approved"
    assert consent_decision(consent(revoked=NOW), "people_data", "v1") == "consent_revoked"
    assert consent_decision(consent(scopes=frozenset()), "people_data", "v1") == (
        "consent_scope_missing"
    )
    assert consent_decision(consent("v0"), "people_data", "v1") == "consent_text_outdated"


# D-98 / G10 ------------------------------------------------------------------------------------


def test_internal_history_is_off_unless_every_condition_holds() -> None:
    full = InternalHistorySettings(True, True, ("Account notes",), True)
    assert internal_history_allowed(full, False)
    assert not internal_history_allowed(InternalHistorySettings(), False)
    for missing in (
        InternalHistorySettings(False, True, ("f",), True),
        InternalHistorySettings(True, False, ("f",), True),
        InternalHistorySettings(True, True, (), True),
        InternalHistorySettings(True, True, ("f",), False),
    ):
        assert not internal_history_allowed(missing, False)
    with pytest.raises(GuardViolation, match="internal_history_default_on"):
        internal_history_allowed(full, True)


def test_g10_private_facts_never_reach_the_public_renderer() -> None:
    renderable("public", "public")
    renderable("private", "internal")
    renderable("public", "internal")
    with pytest.raises(GuardViolation, match="private_to_public_renderer"):
        renderable("private", "public")
    with pytest.raises(GuardViolation, match="unknown_visibility"):
        renderable("secret", "internal")
    facts = [fact("1"), fact("2", vis="private")]
    assert [f.fact_id for f in for_audience(facts, "public")] == ["1"]
    assert len(for_audience(facts, "internal")) == 2


# G11 -------------------------------------------------------------------------------------------


def test_g11_split_mode_is_routing() -> None:
    tiers = {"x_extract": "extractor", "x_sense": "planner", "x_distill": "planner"}
    agents = {
        "pass2": "x_extract",
        "sense_check": "x_sense",
        "distill": "x_distill",
        "pull": None,
        "other": "x_extract",
    }
    assert routing_violations(agents, tiers, "extractor", "planner") == ()
    lying = {"pass2": "x_sense", "distill": "x_extract", "people": "unknown"}
    assert routing_violations(lying, tiers, "extractor", "planner") == (
        "distill:x_extract->extractor",
        "pass2:x_sense->planner",
        "people:unknown->None",
    )


def test_distilled_claims_may_not_restate_grades() -> None:
    assert restates_grade("This is Confirmed by filings")
    assert restates_grade("grade b evidence")
    assert not restates_grade("The account may be prioritising integration.")
