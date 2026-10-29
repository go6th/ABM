"""The reference's Working tool / User role rule, the title triage that replaced the keyword
rules, the order of the classifiers, and the lexicon parity changes of 2026-10-29. Synthetic
postings only."""

from __future__ import annotations

from dataclasses import replace

import pytest

from account_intelligence.dev_limits import UNCAPPED
from jobs.classify import classify_rules, title_candidate
from jobs.config import load_jobs_config
from jobs.heuristic import HeuristicRules, classify_heuristic, collapse, platform_windows
from jobs.match import match_platforms
from jobs.sentences import extract_platform_sentences
from jobs.stage import run_jobs
from tests.fakes.jobs import Corpus, FakeRunInput, Recorder
from tests.jobs.conftest import build, frozen_config

PROD = load_jobs_config()
RULES = PROD.heuristic
LEXICON = PROD.lexicon
EXACT = PROD.case_sensitive_platforms


def _class(text: str, *platforms: str) -> str:
    return classify_heuristic(text, platforms, LEXICON, RULES, EXACT).label


# ---- the documented behaviour: Working tool words, User role otherwise ---------------------


@pytest.mark.parametrize(
    "text",
    [
        "Advanced proficiency in Microsoft Excel, PowerPoint, and ERP systems such as SAP.",
        "Experience working with CRM systems, preferably Salesforce (SFDC).",
        "You will update records in Salesforce every day.",
        "Maintain master data in SAP.",
        "Knowledge of ServiceNow is a plus.",
        "Familiar with Tableau dashboards.",
        "Log every call in Salesforce.",
        "Skills in Veeva Vault required.",
        "Responsible for data entry into Workday.",
        "Administers Salesforce users.",
        "Manage Salesforce accounts.",
        "Utilizes SAP for month end close.",
        "Posting transactions in SAP S/4HANA.",
        "Document processes in ServiceNow.",
        "Enter orders using Salesforce.",
        "Provide input to MuleSoft design.",
    ],
)
def test_a_cue_in_the_platform_sentence_is_a_working_tool(text: str) -> None:
    found = match_platforms("", text, LEXICON, EXACT).description
    assert found, text
    assert _class(text, found[0]) == "working_tool", text


@pytest.mark.parametrize(
    "text",
    [
        "Our teams love Salesforce.",
        "Salesforce is a plus for this role.",
        "The role is based in Heidelberg and Salesforce is mentioned once.",
        "SAP.",
    ],
)
def test_no_cue_in_the_platform_sentence_is_a_user_role(text: str) -> None:
    found = match_platforms("", text, LEXICON, EXACT).description
    assert found
    assert _class(text, found[0]) == "user"


def test_the_window_is_from_the_previous_dot_or_star_to_the_next_dot() -> None:
    text = (
        "We manage the whole business. Benefits include health plans * Free lunch at Salesforce "
        "HQ\n\nand a gym. Experience required."
    )
    assert platform_windows(text, "Salesforce", (), RULES) == (
        "Free lunch at Salesforce HQ and a gym.",
    )
    # the cue "manage" sits in another sentence, "Experience" in the next: neither is in the window
    assert _class(text, "Salesforce") == "user"
    # a bullet mark starts a window; the previous dot ends the one before it
    star = "Intro. * Strong skills in Tableau * Free parking."
    assert platform_windows(star, "Tableau", (), RULES) == (
        "Strong skills in Tableau * Free parking.",
    )


def test_the_cue_must_be_in_a_window_of_a_named_platform_only() -> None:
    text = "Manage the team. Tableau is used here. Experience wanted."
    assert _class(text, "Tableau") == "user"
    # a platform that is not named has no window: the title-only posting is a User role
    assert _class(text, "Salesforce") == "user"
    assert _class("", "Salesforce") == "user"


def test_the_windows_are_capped_per_platform_and_in_characters() -> None:
    rules = HeuristicRules(working_tool_cues=("manage",), max_windows_per_platform=2, max_chars=60)
    text = "Salesforce one. Salesforce two. Salesforce three manage."
    windows = platform_windows(text, "Salesforce", (), rules)
    assert windows == ("Salesforce one.", "Salesforce two.")
    assert classify_heuristic(text, ("Salesforce",), {}, rules).label == "user"
    long_text = ("Salesforce filler. " * 3) + "Salesforce manage."
    capped = replace(rules, max_windows_per_platform=9, max_chars=40)
    assert classify_heuristic(long_text, ("Salesforce",), {}, capped).label == "user"
    assert (
        classify_heuristic(long_text, ("Salesforce",), {}, replace(capped, max_chars=500)).label
        == "working_tool"
    )


def test_whitespace_is_collapsed_and_the_verdict_names_its_evidence() -> None:
    assert collapse("a \n\n b\t c") == "a b c"
    verdict = classify_heuristic(
        "Plain line.\nSalesforce\nusing flows.\nMore.", ("Salesforce",), {}, RULES
    )
    assert verdict.label == "working_tool" and verdict.evidence == "Salesforce using flows."
    quiet = classify_heuristic("Salesforce is nice.", ("Salesforce",), {}, RULES)
    assert quiet.label == "user" and quiet.evidence == "Salesforce is nice."


def test_the_heuristic_answers_working_tool_or_user_only() -> None:
    for text in ("The Salesforce developer builds flows.", "The SAP administrator owns SAP."):
        assert _class(text, "Salesforce" if "Salesforce" in text else "SAP") in {
            "working_tool",
            "user",
        }


# ---- the rule layer is title triage, never a class -----------------------------------------


def test_title_triage_picks_build_and_admin_candidates_only() -> None:
    rules = PROD.rules
    assert title_candidate("Salesforce Developer", rules)
    assert title_candidate("ServiceNow Platform Owner", rules)
    assert title_candidate("SAP Basis Engineer", rules)
    assert not title_candidate("Sales Representative", rules)
    assert not title_candidate("Financial Analyst", rules)
    assert not title_candidate("Customer Service Agent", rules)
    # a keyword in the description is no title: classify_rules still reads text for the old tests,
    # but the pipeline never uses it as a class any more (see the golden run)
    assert classify_rules("As an engineer you run payroll", rules).label == "build"


async def _run(corpus: Corpus, **kw):  # type: ignore[no-untyped-def]
    pipeline, source, pages, first, second = build(corpus, **kw)
    rec = Recorder()
    result = await run_jobs(
        "run-1", pipeline, rec, rec, rec, rec, FakeRunInput(corpus.lanes_off, corpus.as_of)
    )
    return result, first, second


@pytest.mark.asyncio
async def test_a_keyword_in_the_description_no_longer_decides_a_class(corpus: Corpus) -> None:
    """j4 is a "sales representative" who logs calls: the old rule made that a User role; the
    heuristic reads the sentence ("log") and the extractor never saw it (title has no cue)."""
    result, first, _ = await _run(corpus)
    j4 = {p.posting_key: p for p in result.postings}["id:j4"]
    assert j4.classification is not None
    assert (j4.posting_class, j4.classification.classified_by) == ("working_tool", "heuristic")
    assert j4.classification.reason == "heuristic_window"
    assert "id:j4" not in first.calls and "id:j3" not in first.calls and "id:j12" not in first.calls


@pytest.mark.asyncio
async def test_only_a_reading_confirms_build_and_admin_and_owner(corpus: Corpus) -> None:
    result, first, second = await _run(corpus)
    for p in result.postings:
        if p.classification and p.posting_class in ("build", "admin_owner"):
            assert p.classification.classified_by == "extractor", p.posting_key
        if p.classification and p.classification.classified_by == "heuristic":
            assert p.posting_class in ("working_tool", "user"), p.posting_key
    # build and admin and owner needed the second pass on a different tier
    assert set(second.calls) >= {"id:j1", "id:j2", "id:j5", "id:j8"}


@pytest.mark.asyncio
async def test_an_extractor_with_no_verified_class_falls_back_to_the_heuristic(
    corpus: Corpus,
) -> None:
    result, _, _ = await _run(corpus)
    by = {p.posting_key: p for p in result.postings}
    # j6: first pass build, second pass user: disagreement; j17: extractor unclear
    for key, why in (("id:j6", "disagreement"), ("id:j17", "extractor_unclear")):
        c = by[key].classification
        assert c is not None and c.classified_by == "heuristic"
        assert c.reason == f"heuristic_after_{why}"
        assert by[key].posting_class == "user"
    # every read posting that names a platform has a class of the four
    assert {
        p.posting_class for p in result.postings if p.read and p.platforms and not p.hostile
    } <= {"build", "admin_owner", "working_tool", "user"}


@pytest.mark.asyncio
async def test_caps_off_the_extractor_reads_every_posting(corpus: Corpus) -> None:
    cfg = frozen_config()
    wide = replace(cfg, caps={**cfg.caps, "max_extractor_calls": UNCAPPED})
    result, first, _ = await _run(corpus, cfg=wide)
    assert {"id:j3", "id:j4", "id:j12"} <= set(first.calls)  # no title cue needed with caps off
    by = {p.posting_key: p for p in result.postings}
    # the scripted extractor has no answer for them: unclear, then the heuristic
    assert by["id:j4"].classification is not None
    assert by["id:j4"].classification.reason == "heuristic_after_extractor_unclear"


@pytest.mark.asyncio
async def test_a_heuristic_class_makes_no_claim(corpus: Corpus) -> None:
    result, _, _ = await _run(corpus)
    heuristic_keys = {
        p.posting_key
        for p in result.postings
        if p.classification and p.classification.classified_by == "heuristic"
    }
    assert heuristic_keys
    claimed = " ".join(c.reasoning for c in result.claims)
    assert "by heuristic" not in claimed
    assert all(
        c.attribute_key.rsplit(".", 1)[-1] in {"build", "admin_owner"} for c in result.claims
    )


# ---- lexicon parity with the reference's platform patterns ----------------------------------


def _named(text: str, title: str = "") -> set[str]:
    return set(match_platforms(title, text, LEXICON, EXACT).description)


def test_workday_is_a_lexicon_platform_in_the_lane_other() -> None:
    assert PROD.lane_of("Workday") == "other"
    assert "Workday" in _named("Maintain employee data in Workday.")
    other = next(lane for lane in PROD.lanes if lane.id == "other")
    assert other.keywords == ("Workday",) and other.people_search is False
    assert all(lane.people_search for lane in PROD.lanes if lane.id != "other")


def test_sap_is_matched_in_its_own_case_only() -> None:
    assert "SAP" in _named("Experience with SAP and Oracle.")
    assert "SAP" not in _named("a sap of the maple tree, see sap flow")
    assert "SAP" not in _named("Concur with the team on the Ariba of the day.".lower())
    assert "SAP" not in _named("We concur with you.")  # the word, not the product
    for text in (
        "Submit expenses in Concur.",
        "Source in Ariba.",
        "SuccessFactors learning.",
        "S/4HANA.",
    ):
        assert "SAP" in _named(text), text
    assert (
        "SAP" in PROD.case_sensitive_platforms and "Salesforce" not in PROD.case_sensitive_platforms
    )


def test_veeva_counts_etmf_and_ctms_and_salesforce_not_apex() -> None:
    assert "Veeva" in _named("Run the eTMF and CTMS for studies.")
    assert "Salesforce" not in _named("You write Apex classes and triggers.")
    assert "Salesforce" in _named("Health Cloud and Commerce Cloud projects.")
    assert "Salesforce" in _named("Experience Cloud and Field Service Lightning.")


def test_the_sentences_follow_the_same_case_rule() -> None:
    text = "We pour sap. Our SAP team runs it."
    found = extract_platform_sentences(text, ("SAP",), LEXICON, 600, EXACT)
    assert [s.text for s in found] == ["Our SAP team runs it."]
    loose = extract_platform_sentences(text, ("SAP",), LEXICON, 600)
    assert len(loose) == 2
