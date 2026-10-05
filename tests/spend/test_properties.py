"""Property tests (spec 9.3, hypothesis): money never takes a float and never rounds a cost down,
parts sum exactly; priority funding is deterministic, never exceeds what remains and never skips
an affordable higher row for a lower one; the gate never allows more than remains; the report is a
pure function of its rollup; approval binding rejects any mutated hash or principal; rollup parts
always sum to the ledger total."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from spend.approval import VersionRecord, check_binding
from spend.config import load_spend_config
from spend.errors import ApprovalInvalid, ProposalStale
from spend.gate import RunCapState, decide
from spend.models import Principal, Rollup
from spend.money import MoneyError, round_up_to_step, total, usd_to_micros
from spend.priority import PRIORITY_RANK, Candidate, fund, fund_indexed, rank_key
from spend.report import build_report
from spend.rollup import EventRow, aggregate

CFG = load_spend_config()
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
MONEY = st.decimals(min_value=0, max_value=10_000, places=9, allow_nan=False, allow_infinity=False)


@given(MONEY)
def test_a_cost_never_rounds_down(value: Decimal) -> None:
    micros = usd_to_micros(value)
    assert Decimal(micros) / 1_000_000 >= value
    assert Decimal(micros - 1) / 1_000_000 < value or micros == 0


@given(MONEY, MONEY)
def test_rounding_is_monotone(a: Decimal, b: Decimal) -> None:
    lo, hi = sorted((a, b))
    assert usd_to_micros(lo) <= usd_to_micros(hi)


@given(st.floats(allow_nan=True, allow_infinity=True))
def test_a_float_is_never_money(value: float) -> None:
    with pytest.raises(MoneyError):
        usd_to_micros(value)


@given(st.lists(st.integers(min_value=0, max_value=10**12), max_size=50))
def test_parts_sum_exactly(parts: list[int]) -> None:
    assert total(parts) == sum(parts)
    assert total(parts) == total(reversed(parts))


@given(st.integers(min_value=0, max_value=10**10), st.integers(min_value=1, max_value=10**7))
def test_step_rounding_is_up_and_a_multiple(amount: int, step: int) -> None:
    out = round_up_to_step(amount, step)
    assert out >= amount and out % step == 0 and out - amount < step


CANDIDATE = st.builds(
    Candidate,
    row_key=st.sampled_from(["a", "b", "c", "d"]),
    priority=st.sampled_from(list(PRIORITY_RANK)),
    stage_index=st.integers(min_value=0, max_value=3),
    operation=st.sampled_from(["web_search", "read_page"]),
    cost_micros=st.integers(min_value=0, max_value=50_000),
)


@settings(max_examples=300)
@given(
    st.lists(CANDIDATE, max_size=20),
    st.integers(min_value=-10, max_value=200_000),
    st.randoms(use_true_random=False),
)
def test_priority_funding(cands: list[Candidate], remaining: int, rnd: object) -> None:
    result = fund(cands, remaining)
    shuffled = list(cands)
    rnd.shuffle(shuffled)  # type: ignore[attr-defined]
    assert fund(shuffled, remaining) == result  # deterministic, input order irrelevant
    spent = sum(c.cost_micros for c in result.funded)
    assert spent <= max(remaining, 0) and result.remaining_micros == max(remaining, 0) - spent
    # never skipped an affordable higher candidate: each unfunded one did not fit what was left
    # when its turn came in rank order
    left = max(remaining, 0)
    for c in sorted(cands, key=rank_key):
        if c in result.funded and c.cost_micros <= left:
            left -= c.cost_micros
        elif c in result.unfunded:
            assert c.cost_micros > left
    funded, unfunded = fund_indexed(cands, remaining)
    assert sorted(funded + unfunded) == list(range(len(cands)))
    assert sum(cands[i].cost_micros for i in funded) == spent


@given(
    st.booleans(),
    st.one_of(st.none(), st.integers(min_value=1, max_value=5)),
    st.one_of(st.none(), st.integers(min_value=0, max_value=10**7)),
    st.integers(min_value=0, max_value=10**7),
    st.integers(min_value=0, max_value=10),
    st.integers(min_value=0, max_value=10**7),
    st.booleans(),
)
def test_the_gate_never_allows_more_than_remains(
    required: bool,
    version: int | None,
    budget: int | None,
    spent: int,
    over: int,
    estimate: int,
    known: bool,
) -> None:
    state = RunCapState(required, version, budget, spent, over, {})
    d = decide(state, "p_x:op", estimate, known, frozenset())
    if d.allowed and d.reason == "allowed":
        assert budget is not None and version is not None and over == 0 and known
        assert estimate <= budget - spent
    if required and (version is None or budget is None):
        assert d.reason == "no_cap" and not d.allowed


EVENT = st.builds(
    EventRow,
    provider_code=st.sampled_from(["p_search", "p_scrape"]),
    operation=st.sampled_from(["web_search", "scrape_job_page"]),
    route=st.none(),
    args_hash=st.sampled_from(["h1", "h2", "h3"]),
    attempt=st.integers(min_value=1, max_value=2),
    event=st.sampled_from(["started", "done", "failed", "abandoned"]),
    reserved_micros=st.integers(min_value=0, max_value=10_000),
    charged_micros=st.integers(min_value=0, max_value=10_000),
    credits=st.none(),
    cost_source=st.sampled_from(["documented", "unknown", "prepulled"]),
    possible_duplicate=st.booleans(),
)


@given(st.lists(EVENT, max_size=30))
def test_every_grouping_sums_to_the_same_total(rows: list[EventRow]) -> None:
    totals = {
        sum(x.spent_micros for x in aggregate(rows, key))
        for key in (lambda r: r.provider_code, lambda r: r.operation, lambda r: "all")
    }
    assert len(totals) == 1


@given(st.lists(EVENT, max_size=20), st.integers(min_value=0, max_value=10**6))
def test_the_report_is_a_pure_function_of_its_rollup(rows: list[EventRow], cap: int) -> None:
    lines = aggregate(rows, lambda r: r.provider_code)
    ledger = sum(x.spent_micros for x in lines)
    rollup = Rollup(
        run_id="r",
        account_domain="synthetic.example",
        status="COMPLETED",
        attended=False,
        cap_micros=cap or None,
        cap_versions=((1, cap or 1, "human:u", None),),
        spent_micros=ledger,
        ledger_micros=ledger,
        overshoot_micros=0,
        by_provider=lines,
        by_category=lines,
        by_operation=lines,
        advisory={"p_search": cap},
        blocked_rows={},
        extensions=0,
        llm_micros=None,
        balances_start=(),
        balances_end=(),
        estimated_avoided_micros=None,
    )
    a, b = build_report(rollup, (), 25), build_report(rollup, (), 25)
    assert a == b and a.content_hash == b.content_hash and a.markdown == b.markdown
    assert a.body["llm_usd_informational"] == "not measured"  # missing data is never zero


HUMAN = Principal(kind="human", subject="u", tenant_id="t1", role="admin")
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


@given(
    st.text(alphabet="0123456789abcdef", min_size=64, max_size=64).filter(lambda h: h != "a" * 64)
)
def test_binding_rejects_any_other_hash(digest: str) -> None:
    with pytest.raises(ProposalStale):
        check_binding(
            RECORD,
            HUMAN,
            digest,
            NOW,
            CFG,
            run_budget_micros=None,
            spent_micros=0,
            current_cap_micros=None,
        )


@given(st.sampled_from(["tenant_id", "kind", "role"]), st.text(min_size=1, max_size=8))
def test_binding_rejects_any_mutated_principal(field: str, value: str) -> None:
    update: dict[str, object] = {field: value}
    if field == "kind":
        update = {"kind": "agent"}
    if update.get(field) == getattr(HUMAN, field):
        return
    with pytest.raises(ApprovalInvalid):
        check_binding(
            RECORD,
            HUMAN.model_copy(update=update),
            "a" * 64,
            NOW,
            CFG,
            run_budget_micros=None,
            spent_micros=0,
            current_cap_micros=None,
        )


@given(st.integers(min_value=1, max_value=10**6))
def test_binding_rejects_an_expired_proposal(extra_minutes: int) -> None:
    late = NOW + timedelta(minutes=CFG.approval_ttl_minutes + extra_minutes)
    with pytest.raises(ProposalStale):
        check_binding(
            RECORD,
            HUMAN,
            "a" * 64,
            late,
            CFG,
            run_budget_micros=None,
            spent_micros=0,
            current_cap_micros=None,
        )
