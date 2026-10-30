"""Kill tests for the second mutation run (2026-10-30) of ``spend``: the exact text of each
refusal (an operator and a log read it; a changed word is a changed contract), the floor of the
funding amount and the formatting corner the earlier tests left open. Hermetic."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from spend.approval import VersionRecord, check_binding, check_principal
from spend.errors import ApprovalInvalid, ProposalStale
from spend.gate import decide
from spend.models import Principal
from spend.money import (
    MoneyError,
    credits,
    format_usd,
    micros_to_usd,
    round_up_to_step,
    total,
    usd_to_micros,
)
from spend.priority import Candidate, fund, fund_indexed
from tests.spend.test_mutation_kills import CFG, HUMAN, NOW, POLICY, RECORD, _state


def _message(call) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises((MoneyError, ValueError, ApprovalInvalid, ProposalStale)) as caught:
        call()
    return str(caught.value)


# ---- money ------------------------------------------------------------------------------------


def test_money_refusals_say_exactly_this() -> None:
    assert _message(lambda: usd_to_micros(1.5)) == "float is not money"
    assert _message(lambda: usd_to_micros(Decimal("NaN"))) == "money must be finite"
    assert _message(lambda: usd_to_micros(Decimal("Infinity"))) == "money must be finite"
    assert _message(lambda: usd_to_micros(Decimal("-1"))) == "a cost cannot be negative"
    assert _message(lambda: usd_to_micros("abc")) == "not a decimal: 'abc'"
    assert _message(lambda: usd_to_micros(object())) == "unsupported money type object"
    assert _message(lambda: micros_to_usd(1.5)) == "micros must be an int"  # type: ignore[arg-type]
    assert _message(lambda: micros_to_usd(True)) == "micros must be an int"
    assert _message(lambda: round_up_to_step(5, 0)) == "step must be positive"
    assert _message(lambda: round_up_to_step(-1, 5)) == "amount must not be negative"
    assert _message(lambda: total([1, 1.5])) == "parts must be int micros"  # type: ignore[list-item]
    assert _message(lambda: credits(-1)) == "credits cannot be negative"


def test_a_negative_amount_is_allowed_only_when_asked() -> None:
    assert usd_to_micros(Decimal("-1.5"), allow_negative=True) == -1_500_000
    assert (
        round_up_to_step(0, 5) == 0 and round_up_to_step(6, 5) == 10 and round_up_to_step(5, 5) == 5
    )


def test_usd_text_keeps_two_to_six_decimals() -> None:
    assert format_usd(0) == "$0.00"
    assert format_usd(1_500_000) == "$1.50"
    assert format_usd(1_234_567) == "$1.234567"
    assert format_usd(10_000_000) == "$10.00"  # zeros of the whole part are not trimmed
    assert format_usd(1_000_100) == "$1.0001"
    assert format_usd(-250_000) == "$-0.25"


# ---- gate and priority ------------------------------------------------------------------------


def test_an_estimate_below_zero_is_refused_with_this_message() -> None:
    assert _message(lambda: decide(_state(), "p:op", -1, True, frozenset())) == (
        "an estimate cannot be negative"
    )


def test_a_cost_below_zero_is_refused_by_both_funders_with_this_message() -> None:
    bad = [Candidate("r", "High", 0, "op", -1)]
    assert _message(lambda: fund(bad, 5)) == "a cost cannot be negative"
    assert _message(lambda: fund_indexed(bad, 5)) == "a cost cannot be negative"


def test_nothing_is_funded_from_an_empty_or_negative_remainder() -> None:
    one = [Candidate("r", "High", 1, "op", 1)]
    free = [Candidate("r", "High", 0, "op", 0)]
    assert fund_indexed(one, 0) == ((), (0,))  # one micro does not fit in none
    assert fund_indexed(one, -5) == ((), (0,))
    assert fund_indexed(free, -5) == ((0,), ())  # a free candidate always fits
    assert fund_indexed(one, 1) == ((0,), ())


# ---- approval ---------------------------------------------------------------------------------

NOTE = {
    "tenant": "principal belongs to another tenant",
    "agent": "an agent cannot approve or extend a cap",
    "role": "role may not approve a cap",
    "policy": "tenant policy may not approve this cap",
    "limit": "cap is above the tenant policy limit",
    "excluded": "a proposal with excluded providers needs a person to approve it",
}


def test_principal_refusals_are_these_exact_sentences() -> None:
    assert (
        _message(
            lambda: check_principal(HUMAN.model_copy(update={"tenant_id": "t2"}), RECORD, CFG, None)
        )
        == NOTE["tenant"]
    )
    assert (
        _message(
            lambda: check_principal(HUMAN.model_copy(update={"kind": "agent"}), RECORD, CFG, None)
        )
        == NOTE["agent"]
    )
    assert (
        _message(
            lambda: check_principal(HUMAN.model_copy(update={"role": "viewer"}), RECORD, CFG, None)
        )
        == NOTE["role"]
    )
    extension = dataclasses.replace(RECORD, is_extension=True)
    assert _message(lambda: check_principal(POLICY, extension, CFG, 900_000)) == NOTE["policy"]
    assert _message(lambda: check_principal(POLICY, RECORD, CFG, 499_999)) == NOTE["limit"]
    assert _message(lambda: check_principal(POLICY, RECORD, CFG, None)) == NOTE["limit"]
    excluded = dataclasses.replace(RECORD, has_exclusions=True)
    assert _message(lambda: check_principal(POLICY, excluded, CFG, 900_000)) == NOTE["excluded"]


def _bind(
    record: VersionRecord = RECORD,
    *,
    hash_: str = "a" * 64,
    now: datetime = NOW,
    spent: int = 0,
    current: int | None = None,
    principal: Principal = HUMAN,
) -> None:
    check_binding(
        record,
        principal,
        hash_,
        now,
        CFG,
        run_budget_micros=None,
        spent_micros=spent,
        current_cap_micros=current,
    )


def test_binding_refusals_are_these_exact_sentences() -> None:
    assert _message(lambda: _bind(hash_="b" * 64)) == "the proposal changed since it was shown"
    decided = dataclasses.replace(RECORD, events=("proposed", "approved"))
    assert _message(lambda: _bind(decided)) == "this cap version was already decided"
    late = NOW + timedelta(minutes=CFG.approval_ttl_minutes + 1)
    assert _message(lambda: _bind(dataclasses.replace(RECORD, snapshot_times=()), now=late)) == (
        "the proposal expired"
    )
    stale = NOW + timedelta(minutes=CFG.max_snapshot_age_minutes, seconds=1)
    assert _message(lambda: _bind(now=stale)) == "the balance snapshot is stale: propose again"
    assert _message(lambda: _bind(spent=RECORD.cap_micros + 1)) == (
        "a cap cannot be lower than the spend already made"
    )
    extension = dataclasses.replace(RECORD, is_extension=True)
    expected = "an extension must be strictly higher than the current cap"
    assert _message(lambda: _bind(extension, current=None)) == expected
    assert _message(lambda: _bind(extension, current=RECORD.cap_micros)) == expected
    _bind(extension, current=RECORD.cap_micros - 1)
    _bind(spent=RECORD.cap_micros)  # a cap equal to the spend is allowed


def test_an_extension_waits_its_own_ttl_and_a_first_proposal_its_own() -> None:
    extension = dataclasses.replace(RECORD, is_extension=True, snapshot_times=())
    first = dataclasses.replace(RECORD, snapshot_times=())
    just_past = NOW + timedelta(minutes=CFG.extension_ttl_minutes + 1)
    if CFG.extension_ttl_minutes < CFG.approval_ttl_minutes:
        assert (
            _message(lambda: _bind(extension, now=just_past, current=1)) == "the proposal expired"
        )
        _bind(first, now=just_past)
