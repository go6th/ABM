"""Kill tests for the mutation run of ``people.level``, ``people.lane``, ``people.priority`` and
``people.si_firms`` (2026-10-30). Synthetic rules and employer names; no database, no network."""

from __future__ import annotations

from people.config import CueRule, LaneTerms, SiFirm, TitleRule
from people.lane import lanes_for, role_lane_for
from people.level import classify_level
from people.priority import priority_for_title
from people.si_firms import firm_of


def _cue(rule_id: str, value: str, *cues: str) -> CueRule:
    return CueRule(rule_id, value, tuple(cues), "", "")


def _rule(
    rule_id: str,
    value: str,
    cues: tuple[str, ...] = (),
    patterns: tuple[str, ...] = (),
    unless: tuple[str, ...] = (),
) -> TitleRule:
    return TitleRule(rule_id, value, cues, patterns, unless, "", "")


# ---- people.level -----------------------------------------------------------------------------


def test_a_rule_keeps_its_longest_cue_when_a_longer_one_comes_first() -> None:
    rules = [_cue("r", "manager", "senior manager", "manager")]
    hit = classify_level("Global Head Senior Manager", rules, ["manager"])
    assert hit is not None and hit.evidence == "Senior Manager"


def test_a_rule_takes_a_longer_cue_that_comes_later() -> None:
    rules = [_cue("r", "director", "director", "associate director")]
    hit = classify_level("Global Head Associate Director", rules, ["director"])
    assert hit is not None and hit.evidence == "Associate Director"


def test_equal_length_cues_keep_the_first_in_the_rules_order() -> None:
    rules = [_cue("r", "chief", "cto", "cio")]
    hit = classify_level("CIO and CTO", rules, ["chief"])
    assert hit is not None and hit.evidence == "CTO"


def test_a_cue_inside_a_longer_cue_that_starts_at_the_same_place_is_set_aside() -> None:
    rules = [_cue("a", "director", "director"), _cue("b", "manager", "director of engineering")]
    hit = classify_level("Director of Engineering", rules, ["director", "manager"])
    assert hit is not None and (hit.level, hit.rule_id) == ("manager", "b")


def test_a_cue_inside_a_longer_cue_that_starts_earlier_is_set_aside() -> None:
    rules = [_cue("a", "vp", "vice president"), _cue("b", "svp", "senior vice president")]
    hit = classify_level("Senior Vice President, Sales", rules, ["svp", "vp"])
    assert hit is not None and (hit.level, hit.evidence) == ("svp", "Senior Vice President")


def test_the_most_senior_of_the_remaining_levels_wins_and_a_tie_keeps_the_first_rule() -> None:
    rules = [_cue("m", "manager", "manager"), _cue("d", "director", "director")]
    hit = classify_level("Manager and Director", rules, ["director", "manager"])
    assert hit is not None and hit.level == "director"
    twin = [_cue("first", "director", "head"), _cue("second", "director", "lead")]
    tied = classify_level("Lead and Head", twin, ["director"])
    assert tied is not None and tied.rule_id == "first"


def test_no_cue_is_no_level() -> None:
    assert classify_level("Analyst", [_cue("r", "director", "director")], ["director"]) is None
    assert classify_level(None, [_cue("r", "director", "director")], ["director"]) is None


# ---- people.lane ------------------------------------------------------------------------------


def test_a_platform_lane_names_the_matching_text() -> None:
    lanes = [
        LaneTerms("sap", ("SAP",), ("sap", "s/4hana")),
        LaneTerms("sf", ("Salesforce",), ("salesforce",)),
    ]
    found = lanes_for("Lead, SAP Basis", "Runs Salesforce teams", lanes)
    assert [(m.lane, m.evidence) for m in found] == [("sap", "SAP"), ("sf", "Salesforce")]
    assert lanes_for(None, None, lanes) == ()
    # the title is read before the headline and one lane is listed once
    both = lanes_for("SAP lead", "SAP architect", lanes)
    assert [(m.lane, m.evidence) for m in both] == [("sap", "SAP")]


def test_the_role_lane_names_the_matching_text_or_none_for_the_default() -> None:
    rules = [
        _rule("l1", "data", cues=("data engineer",)),
        _rule("l2", "apps", patterns=(r"app\w*",)),
    ]
    hit = role_lane_for("Senior Data Engineer", rules, "other", "default")
    assert (hit.lane, hit.rule_id, hit.evidence) == ("data", "l1", "Data Engineer")
    pattern = role_lane_for("Applications Owner", rules, "other", "default")
    assert (pattern.lane, pattern.rule_id, pattern.evidence) == ("apps", "l2", "Applications")
    default = role_lane_for("Pilot", rules, "other", "default")
    assert (default.lane, default.rule_id, default.evidence) == ("other", "default", "")
    assert role_lane_for(None, rules, "other", "default").evidence == ""


# ---- people.priority --------------------------------------------------------------------------


def test_the_priority_names_the_matching_text_or_none_for_the_default() -> None:
    rules = [
        _rule("c", "C", cues=("intern",)),
        _rule("nonit", "C", cues=("finance",), unless=("salesforce",)),
        _rule("a", "A", cues=("salesforce",), patterns=(r"cio\b",)),
    ]
    hit = priority_for_title("Salesforce Lead", rules, "B", "default")
    assert (hit.priority, hit.rule_id, hit.evidence) == ("A", "a", "Salesforce")
    by_pattern = priority_for_title("Deputy CIO", rules, "B", "default")
    assert (by_pattern.priority, by_pattern.evidence) == ("A", "CIO")
    # the unless cue switches the non-IT rule off; the next rule fires
    unless = priority_for_title("Finance Salesforce Lead", rules, "B", "default")
    assert (unless.priority, unless.rule_id) == ("A", "a")
    nonit = priority_for_title("Finance Director", rules, "B", "default")
    assert (nonit.priority, nonit.rule_id, nonit.evidence) == ("C", "nonit", "Finance")
    default = priority_for_title("Pilot", rules, "B", "default")
    assert (default.priority, default.rule_id, default.evidence) == ("B", "default", "")


# ---- people.si_firms --------------------------------------------------------------------------

FIRMS = (SiFirm("Q Corp", ("q",)), SiFirm("Acme", ("acme\\w*",)))


def test_the_employer_name_is_read_to_its_first_three_hundred_characters() -> None:
    near = "a" * 299 + " q"  # the alias starts at character 301
    assert firm_of(near, FIRMS) is None
    far = "a" * 500 + " q"
    assert firm_of(far, FIRMS) is None
    inside = "a" * 297 + " q"  # the alias is the 299th character
    assert firm_of(inside, FIRMS) == "Q Corp"
    assert firm_of("Acme Consulting", FIRMS) == "Acme"
    assert firm_of("", FIRMS) is None
