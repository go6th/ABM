"""Precedence rule ``unread_match`` (platform rule 4; ABM G2): a keyword match or date range not
read in a primary source this run is a Lead, whatever the engine grade; a primary read wins.
GUARANTEE(test_unread_match_is_a_lead_until_read)
"""

from __future__ import annotations

import pytest

from account_intelligence.grading.config import load_grading_config
from account_intelligence.grading.product import ProductInputs, product_grade
from account_intelligence.grading.schema import ProposedSourced

CFG = load_grading_config()


@pytest.mark.parametrize("engine", ["A", "B", "C", "D"])
def test_unread_match_is_a_lead_until_read(engine: str) -> None:
    unread = ProductInputs("graded", engine, False, unread_match=True)  # type: ignore[arg-type]
    assert product_grade(unread, CFG) == ("lead", "unread_match")
    read = ProductInputs("graded", engine, True, unread_match=True)  # type: ignore[arg-type]
    assert product_grade(read, CFG) == ("confirmed", "primary_read")


def test_a_match_without_evidence_or_in_conflict_keeps_the_earlier_rules() -> None:
    assert (
        product_grade(ProductInputs("no_evidence", None, False, unread_match=True), CFG)[0]
        == "hypothesis"
    )
    conflicting = ProductInputs("conflicting", "A", False, unread_match=True)
    # the conflict cap is a ceiling, not a promotion: an unread match stays a Lead (the rule that
    # decided it is still "conflicting"); a conflicting claim that is not a match caps at Probable
    assert product_grade(conflicting, CFG) == ("lead", "conflicting")
    assert product_grade(ProductInputs("conflicting", "A", False), CFG) == (
        "probable",
        "conflicting",
    )
    assert product_grade(ProductInputs("conflicting", "A", True, unread_match=True), CFG) == (
        "probable",
        "conflicting",
    )


def test_unread_match_rule_sits_after_primary_read() -> None:
    order = CFG.precedence.order
    assert order.index("primary_read") < order.index("unread_match") < order.index("engine_a_or_b")


def test_match_origin_is_optional_and_typed() -> None:
    base = {
        "claim_kind": "sourced",
        "local_id": "c1",
        "attribute_key": "company_news.launch",
        "value": "v",
        "evidence": [{"url": "https://a.example/x", "supporting_sentence": "s"}],
    }
    import json

    def parse(raw: dict) -> ProposedSourced:  # type: ignore[type-arg]
        return ProposedSourced.model_validate_json(json.dumps(raw))

    assert parse(base).match_origin is None
    assert parse({**base, "match_origin": "date_range"}).match_origin == "date_range"
    with pytest.raises(ValueError):
        parse({**base, "match_origin": "hunch"})
