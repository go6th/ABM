"""Tests that kill the mutants that survived the first mutation run of money, gate, priority,
approval and report (spec 9.12): exact decisions, boundaries, refusal messages, and a hand-written
golden of the full report body and Markdown for one fixed rollup."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from spend.approval import VersionRecord, check_binding, check_principal
from spend.config import load_spend_config
from spend.errors import ApprovalInvalid, ProposalStale
from spend.gate import Decision, RunCapState, decide
from spend.models import BalanceSnapshot, Principal, Rollup, SpendLine
from spend.money import (
    MoneyError,
    credits,
    format_usd,
    micros_to_usd,
    round_up_to_step,
    total,
    usd_to_micros,
)
from spend.priority import Candidate, rank_key
from spend.report import build_report

CFG = load_spend_config()
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


# ---- money ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (1.5, "float is not money"),
        ("abc", "not a decimal"),
        ([1], "unsupported money type list"),
        ("NaN", "money must be finite"),
        ("-1", "a cost cannot be negative"),
    ],
)
def test_money_refusals_say_why(value: object, message: str) -> None:
    with pytest.raises(MoneyError, match=message):
        usd_to_micros(value)


def test_money_edges() -> None:
    assert credits(0) == 0 and credits("2.5") == Decimal("2.5")
    with pytest.raises(MoneyError, match="credits cannot be negative"):
        credits(-1)
    with pytest.raises(MoneyError, match="micros must be an int"):
        micros_to_usd(1.0)  # type: ignore[arg-type]
    with pytest.raises(MoneyError, match="step must be positive"):
        round_up_to_step(1, 0)
    with pytest.raises(MoneyError, match="amount must not be negative"):
        round_up_to_step(-1, 5)
    with pytest.raises(MoneyError, match="parts must be int micros"):
        total([1, True])  # type: ignore[list-item]
    assert [format_usd(m) for m in (0, 1, 10_000, 1_500_000, 12_000_000, 1_234_567)] == [
        "$0.00",
        "$0.000001",
        "$0.01",
        "$1.50",
        "$12.00",
        "$1.234567",
    ]


# ---- gate -------------------------------------------------------------------------------------


def _state(**kw: object) -> RunCapState:
    base: dict[str, object] = dict(
        cap_required=True,
        cap_version=1,
        budget_micros=100,
        spent_micros=40,
        overshoot_micros=0,
        sample_calls_left={},
    )
    base.update(kw)
    return RunCapState(**base)  # type: ignore[arg-type]


def test_gate_decisions_are_exact() -> None:
    free = frozenset({"p:free"})
    assert decide(_state(defective_free_ops=free), "p:free", 0, True, free) == Decision(
        False, "free_defect"
    )
    assert decide(_state(), "p:free", 0, True, free) == Decision(True, "free")
    assert decide(_state(cap_required=False), "p:op", 999, True, frozenset()) == Decision(
        True, "legacy"
    )
    assert decide(_state(cap_version=None), "p:op", 1, True, frozenset()) == Decision(
        False, "no_cap"
    )
    assert decide(_state(overshoot_micros=1), "p:op", 1, True, frozenset()) == Decision(
        False, "overshoot", 60
    )
    assert decide(_state(), "p:op", 1, False, frozenset()) == Decision(False, "unknown_cost", 60)
    one_left = _state(sample_calls_left={"p:op": 1})
    assert decide(one_left, "p:op", 10, False, frozenset()) == Decision(True, "allowed", 50)
    assert decide(_state(), "p:op", 61, True, frozenset()) == Decision(False, "cap_reached", 60)
    assert decide(_state(), "p:op", 60, True, frozenset()) == Decision(True, "allowed", 0)
    with pytest.raises(ValueError, match="an estimate cannot be negative"):
        decide(_state(), "p:op", -1, True, frozenset())


# ---- priority ---------------------------------------------------------------------------------


def test_an_unknown_priority_ranks_after_optional() -> None:
    known = Candidate("r", "Optional", 0, "op", 1)
    unknown = Candidate("r", "Urgent", 0, "op", 1)
    assert rank_key(unknown) > rank_key(known) and rank_key(unknown)[0] == 3
    with pytest.raises(ValueError, match="a cost cannot be negative"):
        from spend.priority import fund

        fund([Candidate("r", "High", 0, "op", -1)], 5)


# ---- approval ---------------------------------------------------------------------------------


HUMAN = Principal(kind="human", subject="u", tenant_id="t1", role="admin")
POLICY = Principal(kind="tenant_policy", subject="p", tenant_id="t1")
RECORD = VersionRecord(
    run_id="r",
    run_tenant_id="t1",
    version=1,
    cap_micros=500_000,
    proposal_hash="a" * 64,
    created_at=NOW,
    events=("proposed",),
    snapshot_times=(NOW,),
    is_extension=False,
)


def _bind(
    record: VersionRecord = RECORD,
    principal: Principal = HUMAN,
    now: datetime = NOW,
    budget: int | None = None,
    current: int | None = None,
) -> None:
    check_binding(
        record,
        principal,
        "a" * 64,
        now,
        CFG,
        run_budget_micros=budget,
        spent_micros=0,
        current_cap_micros=current,
    )


@pytest.mark.parametrize(
    ("principal", "record", "budget", "message"),
    [
        (HUMAN.model_copy(update={"tenant_id": "t2"}), RECORD, None, "another tenant"),
        (HUMAN.model_copy(update={"kind": "agent"}), RECORD, None, "an agent cannot approve"),
        (HUMAN.model_copy(update={"role": "viewer"}), RECORD, None, "role may not approve"),
        (POLICY, dataclasses.replace(RECORD, is_extension=True), 900_000, "tenant policy may not"),
        (POLICY, RECORD, 499_999, "above the tenant policy limit"),
        (POLICY, dataclasses.replace(RECORD, has_exclusions=True), 900_000, "excluded providers"),
    ],
)
def test_principal_refusals_say_why(
    principal: Principal, record: VersionRecord, budget: int | None, message: str
) -> None:
    with pytest.raises(ApprovalInvalid, match=message):
        check_principal(principal, record, CFG, budget)


def test_approval_boundaries() -> None:
    check_principal(POLICY, RECORD, CFG, 500_000)  # a cap exactly at the policy limit is fine
    _bind(now=NOW + timedelta(minutes=CFG.max_snapshot_age_minutes))  # snapshot exactly at age
    no_snaps = dataclasses.replace(RECORD, snapshot_times=())
    _bind(record=no_snaps, now=NOW + timedelta(minutes=CFG.approval_ttl_minutes))  # exactly ttl
    # a first proposal waits the approval ttl, not the (shorter) extension ttl
    _bind(record=no_snaps, now=NOW + timedelta(minutes=CFG.extension_ttl_minutes + 1))
    with pytest.raises(ProposalStale, match="the proposal expired"):
        _bind(record=no_snaps, now=NOW + timedelta(minutes=CFG.approval_ttl_minutes + 1))
    with pytest.raises(ProposalStale, match="the proposal changed"):
        check_binding(
            RECORD,
            HUMAN,
            "b" * 64,
            NOW,
            CFG,
            run_budget_micros=None,
            spent_micros=0,
            current_cap_micros=None,
        )
    with pytest.raises(ApprovalInvalid, match="already decided"):
        _bind(record=dataclasses.replace(RECORD, events=("proposed", "approved")))
    with pytest.raises(ProposalStale, match="balance snapshot is stale"):
        _bind(now=NOW + timedelta(minutes=CFG.max_snapshot_age_minutes, seconds=1))


# ---- report: a hand-written golden ------------------------------------------------------------


def _line(key: str) -> SpendLine:
    return SpendLine(
        key=key,
        paid_calls=4,
        replays=None,
        failed_after_charge=1,
        open_reserves=0,
        credits=Decimal("3"),
        spent_micros=25_000,
        unknown_cost_calls=0,
        possible_duplicates=1,
        at_risk_micros=5_000,
        prepulled_calls=2,
        zero_charge_calls=1,
    )


def _rollup(advisory: int) -> Rollup:
    return Rollup(
        run_id="r1",
        account_domain="synthetic.example",
        status="BUDGET_EXCEEDED",
        attended=True,
        cap_micros=100_000,
        cap_versions=((1, 100_000, None, None),),
        spent_micros=25_000,
        ledger_micros=25_000,
        overshoot_micros=0,
        by_provider=(_line("p_search"),),
        by_category=(_line("pages_read"),),
        by_operation=(_line("web_search:direct"),),
        advisory={"p_search": advisory},
        blocked_rows={"budget_cap": ("b", "a")},
        extensions=0,
        llm_micros=None,
        balances_start=(
            BalanceSnapshot(
                provider_code="p_search",
                status="ok",
                balance=Decimal(1),
                unit="credit",
                source="synthetic",
                taken_at=NOW,
            ),
        ),
        balances_end=(),
        estimated_avoided_micros=None,
    )


def _expected_line(key: str, advisory: str, variance: str, flag: bool) -> dict[str, object]:
    return {
        "key": key,
        "paid_calls": 4,
        "prepulled_calls": 2,
        "zero_charge_calls": 1,
        "replays": "not measured",
        "failed_after_charge": 1,
        "open_reserves": 0,
        "credits": "3",
        "usd": "$0.025",
        "share": "100%",
        "advisory_usd": advisory,
        "variance": variance,
        "flag": flag,
        "unknown_cost_calls": 0,
        "possible_duplicates": 1,
        "at_risk_usd": "$0.005",
    }


def test_report_golden_body_and_markdown() -> None:
    # advisory $0.02, spent $0.025: |0.005| is exactly 25% of 0.02, not more: no flag
    report = build_report(_rollup(20_000), (), 25)
    nm = "not measured"
    assert report.status == "reconciled"
    assert report.body == {
        "run_id": "r1",
        "account_domain": "synthetic.example",
        "final_status": "BUDGET_EXCEEDED",
        "attended": True,
        "cap_versions": [{"version": 1, "cap_usd": "$0.10", "approved_by": nm, "at": nm}],
        "totals": {
            "spent_usd": "$0.025",
            "ledger_usd": "$0.025",
            "cap_usd": "$0.10",
            "remaining_usd": "$0.075",
            "percent_of_cap": "25%",
            "overshoot_usd": "$0.00",
        },
        "by_provider": [_expected_line("p_search", "$0.02", "$0.005", False)],
        "by_category": [_expected_line("pages_read", nm, nm, False)],
        "by_operation": [_expected_line("web_search:direct", nm, nm, False)],
        "blocked_rows": {"budget_cap": ["b", "a"]},
        "extensions": 0,
        "llm_usd_informational": nm,
        "balances": {"start": ["p_search:ok"], "end": []},
        "estimated_avoided_usd": nm,
        "reconciliation": ["consistent"],
    }
    assert report.markdown == (
        "# Spend report (internal) for run r1\n\n"
        "Status: reconciled. Final run state: BUDGET_EXCEEDED.\n\n"
        "Spent $0.025 of a cap of $0.10 (25%); remaining $0.075; overshoot $0.00.\n\n"
        "| Provider | Paid calls | Replays | Spent | Share | Advisory | Variance | Flag |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| p_search | 4 | not measured | $0.025 | 100% | $0.02 | $0.005 | no |\n\n"
        "Reconciliation: consistent.\n"
    )


def test_report_flag_boundaries() -> None:
    def flag(advisory: int, spent: int = 25_000) -> object:
        r = _rollup(advisory)
        line = _line("p_search").model_copy(update={"spent_micros": spent})
        r = r.model_copy(update={"by_provider": (line,)})
        body = build_report(r, (), 25).body
        return body["by_provider"][0]["flag"]  # type: ignore[index]

    assert flag(19_999) is True  # just over 25%
    assert flag(1, spent=1) is False  # a one-micro advisory still uses the ratio
    assert flag(0, spent=1) is True and flag(0, spent=0) is False
    marked = build_report(_rollup(19_999), ("spent 1 != ledger 2",), 25)
    assert marked.status == "reconciliation_failed"
    assert (
        "| yes |" in marked.markdown and "Reconciliation: spent 1 != ledger 2." in marked.markdown
    )


def test_more_boundaries() -> None:
    from spend.priority import fund_indexed

    with pytest.raises(ValueError, match="a cost cannot be negative"):
        fund_indexed([Candidate("r", "High", 0, "op", -1)], 5)
    ext = dataclasses.replace(RECORD, is_extension=True, snapshot_times=())
    _bind(record=ext, current=400_000, now=NOW + timedelta(minutes=CFG.extension_ttl_minutes))
    with pytest.raises(ProposalStale, match="the proposal expired"):  # the extension ttl applies
        _bind(
            record=ext, current=400_000, now=NOW + timedelta(minutes=CFG.extension_ttl_minutes + 1)
        )
    check_binding(
        RECORD,
        HUMAN,
        "a" * 64,
        NOW,
        CFG,
        run_budget_micros=None,
        spent_micros=500_000,
        current_cap_micros=None,
    )  # a cap equal to spend is fine


def test_measured_values_are_shown_and_missing_ones_say_so() -> None:
    line = _line("p_search").model_copy(update={"replays": 3, "credits": None})
    r = _rollup(20_000).model_copy(update={"by_provider": (line,), "llm_micros": 7})
    body = build_report(r, (), 25).body
    shown = body["by_provider"][0]  # type: ignore[index]
    assert shown["replays"] == 3 and shown["credits"] == "not measured"
    assert body["llm_usd_informational"] == "$0.000007"


def test_stale_finds_only_open_attempts_older_than_the_lease() -> None:
    from spend.reconcile import stale
    from spend.rollup import EventRow

    def ev(h: str, event: str) -> EventRow:
        return EventRow(
            provider_code="p_search",
            operation="web_search",
            route=None,
            args_hash=h,
            attempt=1,
            event=event,
            reserved_micros=5_000,
            charged_micros=0,
            credits=None,
            cost_source=None,
            possible_duplicate=False,
        )

    rows = [
        ev("old", "started"),
        ev("new", "started"),
        ev("closed", "started"),
        ev("closed", "done"),
    ]
    times = {
        ("old", 1): NOW - timedelta(seconds=61),
        ("new", 1): NOW - timedelta(seconds=60),
        ("closed", 1): NOW - timedelta(days=1),
    }
    assert [r.args_hash for r in stale(rows, times, NOW, 60)] == ["old"]  # exactly at ttl: fresh
    assert stale(rows, {}, NOW, 60) == []  # no start time known: never called stale
