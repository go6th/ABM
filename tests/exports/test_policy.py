"""The B-rule functions in ``exports.policy`` (pure; 100% coverage target)."""

from __future__ import annotations

import pytest

from account_intelligence.grading.config import GradingConfig
from exports.config import Abbreviation, Abbreviations, ContentRules, StalePhrases
from exports.content import FactRow
from exports.policy import (
    authored_violations,
    fact_violations,
    guarded_names,
    is_formula_like,
    leak_hits,
    quote_withheld,
    sanitise,
    split_for_cell,
    visibility_violations,
)
from tests.exports.corpus import broker


def _rules(rules: ContentRules, **update: object) -> ContentRules:
    return rules.model_copy(update=update)


def _fact(**kw: object) -> FactRow:
    base: dict[str, object] = dict(
        id="f1",
        attribute_key="tech.x",
        value_text="v",
        lane=None,
        claim_kind="sourced",
        product_grade="confirmed",
        engine_grade="A",
        read_in_primary_this_run=True,
        may_state_to_prospect=True,
        confirming_question=None,
    )
    base.update(kw)
    return FactRow.model_validate(base)


class TestSanitise:
    def test_removes_xml_invalid_and_control_keeps_tab_newline_rtl_emoji(self) -> None:
        text = "a\x00b\x0bc\x1fd\x7fe\x85f\tg\nh شח \U0001f680 ￾￿"
        assert sanitise(text) == "abcdef\tg\nh شח \U0001f680 "

    def test_never_trims_or_reorders(self) -> None:
        assert sanitise("  x  y  ") == "  x  y  "


@pytest.mark.parametrize("text", ["=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx", '=HYPERLINK("u")'])
def test_formula_like(text: str) -> None:
    assert is_formula_like(text)


@pytest.mark.parametrize("text", ["1+1", " =x", "a=b", "", "S-0001"])
def test_not_formula_like(text: str) -> None:
    assert not is_formula_like(text)


class TestSplit:
    def test_short_text_is_one_part(self) -> None:
        assert split_for_cell("abc", 5) == ("abc",)

    def test_parts_join_back_exactly_and_respect_the_limit(self) -> None:
        text = "x" * 23 + "ش" * 5
        parts = split_for_cell(text, 7)
        assert "".join(parts) == text
        assert all(len(p) <= 7 for p in parts)
        assert len(parts) == 4

    def test_limit_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            split_for_cell("abc", 0)


class TestAuthored:
    def test_clean_text_passes(self, rules: ContentRules) -> None:
        assert authored_violations("A plain sentence.", rules, "w", is_partner=False) == ()

    def test_em_and_en_dash(self, rules: ContentRules) -> None:
        for text in ("A — B", "2020–2021"):
            v = authored_violations(text, rules, "w", is_partner=False)
            assert [x.code for x in v] == ["banned_dash"] and v[0].item == "dash character"

    def test_spaced_hyphen_is_punctuation_but_compound_words_are_not(
        self, rules: ContentRules
    ) -> None:
        assert [x.item for x in authored_violations("one - two", rules, "w", is_partner=False)] == [
            "spaced hyphen"
        ]
        assert authored_violations("well-known co-op", rules, "w", is_partner=False) == ()

    def test_spaced_hyphen_rule_can_be_switched_off(self, rules: ContentRules) -> None:
        off = _rules(
            rules,
            banned_dashes=rules.banned_dashes.model_copy(
                update={"spaced_hyphen_is_punctuation": False}
            ),
        )
        assert authored_violations("one - two", off, "w", is_partner=False) == ()

    def test_stale_phrase_any_case(self, rules: ContentRules) -> None:
        r = _rules(rules, stale_phrases=StalePhrases(source="test", phrases=("As Of Today",)))
        v = authored_violations("We know this as of today.", r, "w", is_partner=False)
        assert [(x.code, x.item) for x in v] == [("stale_phrase", "As Of Today")]

    def test_abbreviation_needs_expansion_before_first_use(self, rules: ContentRules) -> None:
        r = _rules(
            rules,
            abbreviations=Abbreviations(
                source="test",
                entries=(Abbreviation(abbr="CRM", expansion="customer relationship management"),),
            ),
        )
        bad = authored_violations("The CRM is old.", r, "w", is_partner=False)
        assert [x.item for x in bad] == ["CRM"]
        ok = "The customer relationship management (CRM) system. The CRM is old."
        assert authored_violations(ok, r, "w", is_partner=False) == ()
        late = "The CRM is old; customer relationship management matters."
        assert [x.item for x in authored_violations(late, r, "w", is_partner=False)] == ["CRM"]
        assert authored_violations("CRMS and xCRM", r, "w", is_partner=False) == ()

    def test_partner_framing_only_for_partner_tenants(self, rules: ContentRules) -> None:
        text = "They are leaving Salesforce next year."
        assert authored_violations(text, rules, "w", is_partner=False) == ()
        v = authored_violations(text, rules, "w", is_partner=True)
        assert [(x.code, x.item) for x in v] == [("partner_framing", "leaving Salesforce")]
        v = authored_violations("a SALESFORCE FAILURE", rules, "w", is_partner=True)
        assert [x.item for x in v] == ["Salesforce failure"]


class TestLeaks:
    def test_denylist_broker_and_credits(self) -> None:
        assert leak_hits(f"used {broker('p_scrape')} here")
        assert leak_hits("this cost 40 credits")
        assert leak_hits("credits used so far")

    def test_extra_terms_word_bounded(self) -> None:
        assert leak_hits("Met Jane Roe today", ("Jane Roe",))
        assert not leak_hits("Met Jane Roewood today", ("Jane Roe",))
        assert not leak_hits("nothing here", ("", "  "))

    def test_provider_codes_and_internal_hashes(self) -> None:
        assert leak_hits("via p_search") == ("provider_code",)
        assert leak_hits("x" + "a" * 64) == ()  # glued to a letter: not a token
        assert leak_hits("ref " + "ab" * 32) == ("internal_hash",)
        assert leak_hits("http_p_x and xp_search") == ()

    def test_quote_withheld_is_a_leak_check_on_the_quote(self) -> None:
        assert quote_withheld(f"Data came from {broker('p_people')}.", ())
        assert quote_withheld("Ask Jane Roe.", ("Jane Roe",))
        assert not quote_withheld("A clean quote.", ("Jane Roe",))


class TestFacts:
    def test_valid_confirmed_and_probable(self, grading: GradingConfig) -> None:
        assert fact_violations(_fact(), grading) == ()
        probable = _fact(
            product_grade="probable", read_in_primary_this_run=False, may_state_to_prospect=False
        )
        assert fact_violations(probable, grading) == ()

    def test_unknown_grade_is_refused_alone(self, grading: GradingConfig) -> None:
        v = fact_violations(_fact(product_grade="certain"), grading)
        assert [x.code for x in v] == ["grade_vocabulary"]

    def test_confirmed_without_primary_read(self, grading: GradingConfig) -> None:
        v = fact_violations(_fact(read_in_primary_this_run=False), grading)
        assert [x.code for x in v] == ["confirmed_without_primary_read"]

    def test_statement_flag_must_match_policy(self, grading: GradingConfig) -> None:
        v = fact_violations(
            _fact(
                product_grade="probable", may_state_to_prospect=True, read_in_primary_this_run=False
            ),
            grading,
        )
        assert [x.code for x in v] == ["statement_flag_disagrees"]
        v = fact_violations(_fact(may_state_to_prospect=False), grading)
        assert [x.code for x in v] == ["statement_flag_disagrees"]

    def test_hypothesis_and_conflicting_need_a_question(self, grading: GradingConfig) -> None:
        hyp = _fact(
            product_grade="hypothesis",
            may_state_to_prospect=False,
            read_in_primary_this_run=False,
            confirming_question="  ",
        )
        assert [x.code for x in fact_violations(hyp, grading)] == ["question_missing"]
        conflicting = _fact(flags=("conflicting",))
        assert [x.code for x in fact_violations(conflicting, grading)] == ["question_missing"]
        ok = _fact(flags=("conflicting",), confirming_question="Which value?")
        assert fact_violations(ok, grading) == ()

    def test_internal_grade_never_reaches_a_public_file(self, grading: GradingConfig) -> None:
        internal = _fact(
            product_grade="internal", may_state_to_prospect=False, read_in_primary_this_run=False
        )
        assert "internal_in_public_file" in [x.code for x in fact_violations(internal, grading)]


def test_visibility() -> None:
    v = visibility_violations([("a", "public"), ("b", "private"), ("c", "internal")], "w")
    assert [(x.code, x.item) for x in v] == [("non_public_row", "b"), ("non_public_row", "c")]


def test_guarded_names() -> None:
    names = [
        ("Ann Listed", True),
        ("Bob Excluded", False),
        ("bob excluded", False),
        (" Ann Listed ", False),  # same name as a listed person: not guarded
        ("   ", False),
        ("Cy Merged", False),
    ]
    assert guarded_names(names) == ("Bob Excluded", "bob excluded", "Cy Merged")
