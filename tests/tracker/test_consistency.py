"""Tracker consistency rules (master prompt v2 section 11): a row cannot contradict the content
beside it, the private-company rule is dormant until the account record says private, the
customers row is Blocked until a reference list exists, pass 1 rows without internal access
read "needs account owner", and the tally is recomputed from the adjusted rows. Pure."""

from __future__ import annotations

from account_intelligence.tracker.config import load_checklist, load_tracker_config
from account_intelligence.tracker.consistency import (
    ContentSignals,
    CoverageLine,
    apply_rules,
    tally_sentences,
)
from account_intelligence.tracker.render import STATUS_TEXT

RULES = load_tracker_config().consistency
REASONS = load_tracker_config().reason_codes
DONE, NONE, PARTIAL = STATUS_TEXT["done"], STATUS_TEXT["none"], STATUS_TEXT["partial"]
BLOCKED, NOT_DONE = STATUS_TEXT["blocked"], STATUS_TEXT["not_done"]


def lines(**status: str) -> tuple[CoverageLine, ...]:
    """The 33 rows, Done unless named, no reason."""
    return tuple(
        CoverageLine(
            key=r.key,
            row=r.label,
            status=status.get(r.key, DONE),
            priority=r.priority.capitalize(),
            reason=None,
        )
        for r in load_checklist().rows
    )


def by_key(rows: tuple[CoverageLine, ...]) -> dict[str, CoverageLine]:
    return {r.key: r for r in rows}


def test_the_rules_are_configured_and_name_real_rows() -> None:
    keys = {r.key for r in load_checklist().rows}
    assert RULES["news_row"] in keys and RULES["leadership_row"] in keys
    assert RULES["reference_row"] in keys and set(RULES["private_rows"]) <= keys
    assert "—" not in str(RULES) and "–" not in str(RULES)


def test_a_none_news_row_beside_dated_news_becomes_partial() -> None:
    rows = lines(company_news=NONE)
    kept = by_key(apply_rules(rows, ContentSignals(dated_news=False)))
    assert kept["company_news"].status == NONE
    adjusted = by_key(apply_rules(rows, ContentSignals(dated_news=True)))
    assert adjusted["company_news"].status == PARTIAL
    assert adjusted["company_news"].reason == RULES["news_reason"]
    # a Done news row is left alone
    assert (
        by_key(apply_rules(lines(), ContentSignals(dated_news=True)))["company_news"].status == DONE
    )


def test_a_none_leadership_row_beside_a_recent_change_becomes_partial() -> None:
    rows = lines(leadership_changes=NONE)
    assert by_key(apply_rules(rows, ContentSignals()))["leadership_changes"].status == NONE
    adjusted = by_key(apply_rules(rows, ContentSignals(recent_leadership_change=True)))
    assert adjusted["leadership_changes"].status == PARTIAL
    assert adjusted["leadership_changes"].reason == RULES["leadership_reason"]


def test_private_company_financial_rows_are_none_never_done() -> None:
    rows = lines(earnings_filings=PARTIAL)
    private = by_key(apply_rules(rows, ContentSignals(is_public=False)))
    for key in RULES["private_rows"]:
        assert private[key].status == NONE and private[key].reason == RULES["private_reason"]
    # dormant until the account record says private; a public company is untouched
    for signals in (ContentSignals(is_public=None), ContentSignals(is_public=True)):
        rows_after = by_key(apply_rules(rows, signals))
        assert rows_after["earnings_filings"].status == PARTIAL
        assert rows_after["financial_baseline_verified"].status == DONE


def test_customers_row_is_blocked_until_a_reference_list_exists() -> None:
    blocked = by_key(apply_rules(lines(), ContentSignals()))["customers_partners_matched"]
    assert blocked.status == BLOCKED and blocked.reason == RULES["reference_needs"]
    supplied = by_key(apply_rules(lines(), ContentSignals(reference_list_supplied=True)))
    assert supplied["customers_partners_matched"].status == DONE


def test_pass_one_rows_without_internal_access_need_the_account_owner() -> None:
    no_access = REASONS["no_internal_access"]
    rows = []
    for r in lines():
        if r.key in ("prior_customer_status", "company_news"):
            r = CoverageLine(**{**r.__dict__, "status": BLOCKED, "reason": no_access})
        rows.append(r)
    adjusted = by_key(apply_rules(tuple(rows), ContentSignals()))
    first = adjusted["prior_customer_status"]
    assert first.status == NOT_DONE and first.reason == RULES["no_internal_access_needs"]
    # the wording applies to pass 1 rows only; a pass 2 row keeps the tracker's own reason
    assert (
        adjusted["company_news"].status == BLOCKED and adjusted["company_news"].reason == no_access
    )


def test_the_tally_is_recomputed_from_the_adjusted_rows() -> None:
    before = tally_sentences(lines())
    assert before[0].startswith("High rows:") and " 0 Blocked," in before[0]
    after = tally_sentences(apply_rules(lines(), ContentSignals()))
    assert " 1 Blocked," in after[0]  # the customers row (High)
    assert all(" of " in s for s in after)  # every figure names its base


def test_empty_rules_apply_nothing() -> None:
    rows = lines(company_news=NONE)
    assert apply_rules(rows, ContentSignals(dated_news=True), rules={}) == rows
    assert apply_rules(rows, ContentSignals(), rules={}) == rows


def test_order_is_kept() -> None:
    rows = lines()
    assert [r.key for r in apply_rules(rows, ContentSignals(is_public=False))] == [
        r.key for r in rows
    ]
