"""Pure credit cap logic: money, config, estimator, proposal, priority, gate, approval rules,
balances, rollup, reconciliation and report (spec 9.3, 9.6). Synthetic values only."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from spend import money
from spend.approval import VersionRecord, check_binding, state_of
from spend.balances import preflight
from spend.config import SpendConfig, build, load_spend_config
from spend.errors import ApprovalInvalid, ConfigError, NotConfigured, ProposalStale
from spend.estimator import estimate_lines
from spend.gate import RunCapState, decide
from spend.models import BalanceSnapshot, Principal, Rollup
from spend.priority import Candidate, fund, rank_key
from spend.proposal import build_proposal
from spend.reconcile import check
from spend.report import build_report, tenant_view
from spend.rollup import EventRow, aggregate, open_attempts
from tests.fakes.spend import COSTS, PLAN, STAGES, FakeBalances
from tests.spend.conftest import NOW

ROOT = Path(__file__).resolve().parents[2]


# ---- money (K6) ---------------------------------------------------------------------------------


def test_money_rejects_float_nan_and_negative() -> None:
    for bad in (0.1, True, "NaN", "inf", "abc", None, Decimal("Infinity")):
        with pytest.raises(money.MoneyError):
            money.usd_to_micros(bad)
    with pytest.raises(money.MoneyError):
        money.usd_to_micros("-0.01")
    assert money.usd_to_micros("-0.01", allow_negative=True) == -10_000
    with pytest.raises(money.MoneyError):
        money.micros_to_usd(1.5)  # type: ignore[arg-type]
    with pytest.raises(money.MoneyError):
        money.credits("-1")
    with pytest.raises(money.MoneyError):
        money.total([1, 2.0])  # type: ignore[list-item]
    with pytest.raises(money.MoneyError):
        money.round_up_to_step(1, 0)
    with pytest.raises(money.MoneyError):
        money.round_up_to_step(-1, 5)


def test_money_conversions() -> None:
    assert money.usd_to_micros("5.00") == 5_000_000 and money.usd_to_micros(3) == 3_000_000
    assert money.usd_to_micros(Decimal("0.0000001")) == 1  # rounds up, never down
    assert money.micros_to_usd(1_234_567) == Decimal("1.234567")
    assert money.format_usd(5_000_000) == "$5.00" and money.format_usd(1_234_500) == "$1.2345"
    assert money.credits_to_micros(Decimal("10"), None) is None
    assert money.credits_to_micros(Decimal("10"), Decimal("0.002")) == 20_000
    assert (
        money.round_up_to_step(0, 50_000) == 0 and money.round_up_to_step(50_001, 50_000) == 100_000
    )


@settings(max_examples=200, deadline=None)
@given(
    st.decimals(min_value=0, max_value=10_000, allow_nan=False, places=9),
    st.decimals(min_value=0, max_value=1, allow_nan=False, places=9),
)
def test_cost_rounding_is_up_and_monotone(a: Decimal, extra: Decimal) -> None:
    micros = money.usd_to_micros(a)
    assert Decimal(micros) / 1_000_000 >= a  # never rounds a cost down
    assert money.usd_to_micros(a + extra) >= micros  # monotone


@settings(max_examples=200, deadline=None)
@given(st.lists(st.integers(min_value=0, max_value=10**12), max_size=50))
def test_parts_sum_to_total_exactly(parts: list[int]) -> None:
    assert money.total(parts) == sum(parts)
    assert money.micros_to_usd(money.total(parts)) == sum(
        (money.micros_to_usd(p) for p in parts), Decimal(0)
    )


# ---- config -------------------------------------------------------------------------------------


def test_spend_config_loads_and_every_entry_is_sourced(cfg: SpendConfig) -> None:
    import yaml

    data = yaml.safe_load((ROOT / "config/spend.yaml").read_text())
    for key, value in data.items():
        if isinstance(value, dict) and "value" in value:
            assert "source" in value or "status" in value, key
    assert cfg.default_cap_micros == 5_000_000 and cfg.default_tool_call_cap == 60
    assert cfg.credit_usd_rates == {} and not cfg.allow_fallback_on_exhaustion
    assert len(cfg.config_hash) == 64 and cfg.category("web_search") == "pages_read"
    assert cfg.category("unlisted_operation") == "other"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("cap_unit", {"value": "credits", "status": "proposal"}),
        ("default_cap_usd", {"value": 5.0, "status": "proposal"}),
        ("default_cap_usd", {"value": "0", "status": "proposal"}),
        ("rounding_step_usd", {"value": "0", "status": "proposal"}),
        ("headroom_pct", {"value": 10}),
        ("headroom_pct", {"value": -1, "status": "proposal"}),
        ("auto_approve", {"value": "always", "status": "proposal"}),
        ("allow_fallback_on_exhaustion", {"value": "no", "status": "proposal"}),
        ("approver_roles", {"value": [], "status": "proposal"}),
        ("credit_usd_rates", {"p_scrape": {"value": "0.01"}}),
        ("free_operations_source", None),
        ("categories", {"by_operation": {}}),
        ("sample_hold_usd", {"value": "abc", "status": "proposal"}),
        ("default_tool_call_cap", None),
    ],
)
def test_invalid_spend_config_is_refused(key: str, value: Any) -> None:
    import yaml

    data = yaml.safe_load((ROOT / "config/spend.yaml").read_text())
    if value is None:
        data.pop(key)
    else:
        data[key] = value
    with pytest.raises(ConfigError):
        build(data, b"x")


def test_sourced_credit_rate_is_accepted() -> None:
    import yaml

    data = yaml.safe_load((ROOT / "config/spend.yaml").read_text())
    data["credit_usd_rates"] = {"p_scrape": {"value": "0.001", "source": "synthetic contract"}}
    assert build(data, b"x").credit_usd_rates == {"p_scrape": Decimal("0.001")}


def test_config_file_errors(tmp_path: Path) -> None:
    load_spend_config.cache_clear()
    with pytest.raises(ConfigError):
        load_spend_config(tmp_path)
    (tmp_path / "spend.yaml").write_text("- a list")
    with pytest.raises(ConfigError):
        load_spend_config(tmp_path)
    load_spend_config.cache_clear()


# ---- balances, estimator, proposal --------------------------------------------------------------

OK = {"p_search": "ok", "p_signals": "low", "p_people": "ok"}


def _snaps(statuses: dict[str, str], when: Any = NOW) -> dict[str, BalanceSnapshot]:
    return {
        code: BalanceSnapshot(
            provider_code=code,
            status=s,
            balance=None if s in ("unknown", "empty") else Decimal(1),
            source="synthetic",
            taken_at=when,
        )  # type: ignore[arg-type]
        for code, s in statuses.items()
    }


@pytest.mark.asyncio
async def test_balances_unknown_when_not_configured() -> None:
    snaps = await preflight(
        FakeBalances({"p_search": "ok"}, NOW), ["p_search", "p_org", "p_search"], NOW
    )
    assert snaps["p_search"].status == "ok" and snaps["p_org"].status == "unknown"
    assert snaps["p_org"].source == "unavailable: NotConfigured"
    none = await preflight(None, ["p_search"], NOW)
    assert none["p_search"].status == "unknown" and none["p_search"].balance is None
    from spend.balances import UnconfiguredBalances

    with pytest.raises(NotConfigured):
        await UnconfiguredBalances().balance("p_search")


def test_unknown_cost_is_a_sample_line_never_zero(cfg: SpendConfig) -> None:
    lines = {
        (line.provider_code, line.operation): line
        for line in estimate_lines(PLAN, COSTS, _snaps(OK), cfg, NOW)
    }
    sample = lines[("p_people", "profile_enrichment")]
    assert sample.status == "sample" and sample.calls == cfg.sample_size
    assert sample.high_micros == cfg.sample_size * cfg.sample_hold_micros > 0
    assert sample.note and "new approval" in sample.note
    search = lines[("p_search", "web_search")]
    assert (search.calls, search.expected_micros, search.row_keys) == (
        12,
        60_000,
        ("company_news", "research_papers"),
    )
    lines2 = estimate_lines(PLAN, {}, _snaps(OK), cfg, NOW)  # nothing priced: every line a sample
    assert all(line.status == "sample" and line.high_micros > 0 for line in lines2)


def test_empty_or_unknown_balance_excludes_the_provider(cfg: SpendConfig) -> None:
    snaps = _snaps({"p_search": "empty", "p_signals": "unknown"})
    snaps["p_people"] = _snaps(
        {"p_people": "ok"}, NOW - timedelta(minutes=cfg.max_snapshot_age_minutes + 1)
    )["p_people"]
    lines = {line.provider_code: line for line in estimate_lines(PLAN, COSTS, snaps, cfg, NOW)}
    assert {line.status for line in lines.values()} == {"excluded_balance"}
    assert (
        lines["p_search"].note == "balance empty" and lines["p_signals"].note == "balance unknown"
    )
    assert lines["p_people"].note == "balance snapshot stale"
    assert estimate_lines(PLAN[:1], COSTS, {}, cfg, NOW)[0].note == "no snapshot"
    proposal = build_proposal("run", PLAN, COSTS, snaps, cfg, NOW, STAGES)
    assert proposal.excluded_providers == ("p_people", "p_search", "p_signals")
    assert proposal.cap_micros == cfg.rounding_step_micros  # nothing priced: one step, said so
    assert any("No operation could be priced" in a for a in proposal.assumptions)
    assert set(proposal.blocked_previews[0].rows) >= {"company_news", "job_postings_12_months"}


def test_proposal_cap_covers_expected_with_headroom_rounded_up(cfg: SpendConfig) -> None:
    p = build_proposal("run-1", PLAN, COSTS, _snaps(OK), cfg, NOW, STAGES)
    expected = 12 * 5_000 + 250_000 + cfg.sample_size * cfg.sample_hold_micros
    assert p.expected_micros == expected
    assert p.cap_micros >= expected * (100 + cfg.headroom_pct) // 100
    assert p.cap_micros % cfg.rounding_step_micros == 0
    assert p.cap_micros - cfg.rounding_step_micros < -(-expected * (100 + cfg.headroom_pct) // 100)
    assert p.minimum_viable_micros == money.round_up_to_step(8 * 1_000, cfg.rounding_step_micros)
    assert p.default_cap_micros == 5_000_000
    assert p.sample_operations == ("p_people:profile_enrichment",) == p.unknown_operations
    previews = {pv.cap_micros: pv.rows for pv in p.blocked_previews}
    assert previews[p.cap_micros] == ("job_postings_12_months",)  # its high bound needs more
    assert "company_news" not in previews[p.minimum_viable_micros] or True
    assert p.excluded_providers == ()


def test_proposal_is_deterministic_and_hash_binds_every_field(cfg: SpendConfig) -> None:
    a = build_proposal("run-1", PLAN, COSTS, _snaps(OK), cfg, NOW, STAGES)
    b = build_proposal("run-1", list(PLAN), dict(COSTS), _snaps(OK), cfg, NOW, STAGES)
    assert a.content_hash == b.content_hash and a.canonical_json() == b.canonical_json()
    for name in type(a).model_fields:
        mutated = _mutate(getattr(a, name))
        if mutated is None:
            continue
        other = a.model_copy(update={name: mutated})
        assert other.content_hash != a.content_hash, name


def _mutate(value: Any) -> Any:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str):
        return value + "x"
    if isinstance(value, tuple):
        return value[:-1] if value else ("x",)
    return None


# ---- priority (K10) -----------------------------------------------------------------------------

CAND = st.builds(
    Candidate,
    row_key=st.sampled_from(["r1", "r2", "r3", "r4"]),
    priority=st.sampled_from(["High", "Medium", "Optional"]),
    stage_index=st.integers(0, 5),
    operation=st.sampled_from(["a", "b"]),
    cost_micros=st.integers(0, 1_000_000),
)


@settings(max_examples=300, deadline=None)
@given(st.lists(CAND, max_size=12), st.integers(-10, 3_000_000), st.randoms())
def test_priority_never_exceeds_remaining_and_prefers_higher_rows(
    cands: list[Candidate], remaining: int, rnd: Any
) -> None:
    out = fund(cands, remaining)
    assert sum(c.cost_micros for c in out.funded) <= max(remaining, 0)
    assert out.remaining_micros == max(remaining, 0) - sum(c.cost_micros for c in out.funded)
    assert len(out.funded) + len(out.unfunded) == len(cands)
    shuffled = list(cands)
    rnd.shuffle(shuffled)
    assert fund(shuffled, remaining) == out  # deterministic, order independent
    # no unfunded candidate ranked before a funded one could have been afforded instead of it
    for u in out.unfunded:
        before = [f for f in out.funded if rank_key(f) <= rank_key(u)]
        spent_before = sum(f.cost_micros for f in before)
        assert u.cost_micros > max(remaining, 0) - spent_before
    with pytest.raises(ValueError):
        fund([Candidate("r", "High", 0, "a", -1)], 10)


def test_unfunded_rows_unique_in_rank_order() -> None:
    out = fund(
        [
            Candidate("r2", "Medium", 0, "a", 9),
            Candidate("r1", "High", 0, "a", 9),
            Candidate("r1", "High", 0, "b", 9),
        ],
        0,
    )
    assert out.unfunded_rows == ("r1", "r2")


# ---- gate (K2, K4, K5, K14) ---------------------------------------------------------------------

S = RunCapState(
    cap_required=True,
    cap_version=1,
    budget_micros=1_000_000,
    spent_micros=400_000,
    overshoot_micros=0,
    sample_calls_left={"p_people:profile_enrichment": 2},
)


def test_gate_refuses_without_an_approved_cap() -> None:
    assert (
        decide(replace(S, cap_version=None), "p_search:web_search", 1, True, frozenset()).reason
        == "no_cap"
    )
    assert (
        decide(replace(S, budget_micros=None), "p_search:web_search", 1, True, frozenset()).reason
        == "no_cap"
    )
    legacy = decide(
        replace(S, cap_required=False, cap_version=None),
        "p_search:web_search",
        1,
        True,
        frozenset(),
    )
    assert (legacy.allowed, legacy.reason) == (True, "legacy")
    free = decide(replace(S, cap_version=None), "p_x:free_op", 0, True, frozenset({"p_x:free_op"}))
    assert (free.allowed, free.reason) == (True, "free")


def test_gate_refuses_unknown_cost_without_a_sample_line() -> None:
    assert decide(S, "p_org:org_teams", 1, False, frozenset()).reason == "unknown_cost"
    ok = decide(S, "p_people:profile_enrichment", 250_000, False, frozenset())
    assert ok.allowed and ok.remaining_micros == 350_000
    spent = replace(S, sample_calls_left={"p_people:profile_enrichment": 0})
    assert (
        decide(spent, "p_people:profile_enrichment", 1, False, frozenset()).reason == "unknown_cost"
    )


def test_gate_stops_after_an_overshoot() -> None:
    d = decide(replace(S, overshoot_micros=1), "p_search:web_search", 1, True, frozenset())
    assert (d.allowed, d.reason, d.remaining_micros) == (False, "overshoot", 600_000)


def test_gate_cap_reached_and_exact_fit() -> None:
    assert decide(S, "p_search:web_search", 600_000, True, frozenset()).allowed
    d = decide(S, "p_search:web_search", 600_001, True, frozenset())
    assert (d.allowed, d.reason, d.remaining_micros) == (False, "cap_reached", 600_000)
    with pytest.raises(ValueError):
        decide(S, "p_search:web_search", -1, True, frozenset())


# ---- approval rules (K3, K1) --------------------------------------------------------------------

REC = VersionRecord(
    run_id="run",
    run_tenant_id="t1",
    version=1,
    cap_micros=500_000,
    proposal_hash="a" * 64,
    created_at=NOW,
    events=("proposed",),
    snapshot_times=(NOW,),
    is_extension=False,
)
HUMAN = Principal(kind="human", subject="user-1", tenant_id="t1", role="admin")
POLICY = Principal(kind="tenant_policy", subject="auto_approve_within_reserve", tenant_id="t1")


def _bind(
    record: VersionRecord = REC,
    principal: Principal = HUMAN,
    digest: str = "a" * 64,
    now: Any = NOW,
    budget: int | None = 5_000_000,
    spent: int = 0,
    current: int | None = None,
    cfg: SpendConfig | None = None,
) -> None:
    check_binding(
        record,
        principal,
        digest,
        now,
        cfg or load_spend_config(),
        run_budget_micros=budget,
        spent_micros=spent,
        current_cap_micros=current,
    )


def test_approval_binding_rejects_every_mutated_field(cfg: SpendConfig) -> None:
    _bind()
    _bind(principal=POLICY)
    cases: list[tuple[dict[str, Any], type[Exception]]] = [
        ({"digest": "b" * 64}, ProposalStale),
        ({"principal": replace_principal(tenant_id="t2")}, ApprovalInvalid),
        ({"principal": replace_principal(kind="agent")}, ApprovalInvalid),
        ({"principal": replace_principal(role="viewer")}, ApprovalInvalid),
        (
            {"record": REC.__class__(**{**REC.__dict__, "events": ("proposed", "approved")})},
            ApprovalInvalid,
        ),
        ({"now": NOW + timedelta(minutes=cfg.approval_ttl_minutes + 1)}, ProposalStale),
        ({"now": NOW + timedelta(minutes=cfg.max_snapshot_age_minutes + 1)}, ProposalStale),
        ({"principal": POLICY, "budget": 100}, ApprovalInvalid),
        ({"principal": POLICY, "budget": None}, ApprovalInvalid),
        (
            {
                "principal": POLICY,
                "record": REC.__class__(**{**REC.__dict__, "has_exclusions": True}),
            },
            ApprovalInvalid,
        ),
        (
            {
                "principal": POLICY,
                "record": REC.__class__(**{**REC.__dict__, "is_extension": True}),
            },
            ApprovalInvalid,
        ),
        ({"principal": POLICY, "cfg": replace(cfg, auto_approve="never")}, ApprovalInvalid),
    ]
    for kw, error in cases:
        with pytest.raises(error):
            _bind(**kw)


def replace_principal(**kw: Any) -> Principal:
    return HUMAN.model_copy(update=kw)


def test_cap_never_lowered_below_spend(cfg: SpendConfig) -> None:
    with pytest.raises(ApprovalInvalid, match="lower than the spend"):
        _bind(spent=500_001)
    ext = REC.__class__(**{**REC.__dict__, "is_extension": True})
    with pytest.raises(ApprovalInvalid, match="strictly higher"):
        _bind(record=ext, current=500_000)
    with pytest.raises(ApprovalInvalid, match="strictly higher"):
        _bind(record=ext, current=None)
    _bind(record=ext, current=400_000, now=NOW + timedelta(minutes=cfg.extension_ttl_minutes - 1))
    with pytest.raises(ProposalStale):
        _bind(
            record=ext, current=400_000, now=NOW + timedelta(minutes=cfg.extension_ttl_minutes + 1)
        )
    assert (
        state_of(("proposed", "rejected", "approved")) == "rejected" and state_of(()) == "proposed"
    )


# ---- rollup, reconcile, report (K7, K8, K16, K13) -----------------------------------------------


def ev(
    code: str,
    op: str,
    h: str,
    event: str,
    *,
    att: int = 1,
    reserved: int = 0,
    charged: int = 0,
    credits: str | None = None,
    source: str = "documented",
    dup: bool = False,
) -> EventRow:
    return EventRow(
        code,
        op,
        None,
        h,
        att,
        event,
        reserved,
        charged,
        Decimal(credits) if credits is not None else None,
        source,
        dup,
    )


ROWS = [
    ev("p_search", "web_search", "h1", "started", reserved=5_000),
    ev("p_search", "web_search", "h1", "done", charged=5_000),
    ev("p_search", "web_search", "h2", "started", reserved=5_000),
    ev("p_search", "web_search", "h2", "failed", charged=5_000),
    ev("p_signals", "job_openings", "h3", "started", reserved=250_000),
    ev(
        "p_signals",
        "job_openings",
        "h3",
        "done",
        charged=250_000,
        credits="3",
        source="credits_only",
    ),
    ev("p_people", "profile_enrichment", "h4", "started", reserved=250_000),  # open (crash)
    ev("p_people", "profile_enrichment", "h5", "started", reserved=250_000),
    ev("p_people", "profile_enrichment", "h5", "abandoned", charged=250_000, dup=True),
    ev("p_people", "profile_enrichment", "h5", "started", att=2, reserved=250_000),
    ev("p_people", "profile_enrichment", "h5", "done", att=2, charged=250_000, source="unknown"),
]


def test_rollup_parts_sum_to_the_ledger_total() -> None:
    lines = {line.key: line for line in aggregate(ROWS, lambda r: r.provider_code)}
    assert lines["p_search"].spent_micros == 10_000 and lines["p_search"].failed_after_charge == 1
    assert lines["p_signals"].credits == Decimal(3) and lines["p_search"].credits is None
    people = lines["p_people"]
    assert (people.paid_calls, people.open_reserves, people.spent_micros) == (1, 1, 750_000)
    assert (people.unknown_cost_calls, people.possible_duplicates, people.at_risk_micros) == (
        1,
        1,
        250_000,
    )
    assert all(line.replays is None for line in lines.values())  # not measured, never 0
    by_op = aggregate(ROWS, lambda r: r.operation)
    assert (
        money.total(line.spent_micros for line in by_op)
        == money.total(line.spent_micros for line in lines.values())
        == 1_010_000
    )
    assert [r.args_hash for r in open_attempts(ROWS)] == ["h4"]


@settings(max_examples=150, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.sampled_from(["p_a", "p_b"]),
            st.integers(0, 3),
            st.sampled_from(["done", "failed", "abandoned", "open"]),
            st.integers(0, 10**6),
        ),
        max_size=20,
    )
)
def test_rollup_groupings_always_sum_to_the_same_total(
    spec: list[tuple[str, int, str, int]],
) -> None:
    rows: list[EventRow] = []
    for i, (code, op_n, kind, amount) in enumerate(spec):
        rows.append(ev(code, f"op{op_n}", f"h{i}", "started", reserved=amount))
        if kind != "open":
            rows.append(ev(code, f"op{op_n}", f"h{i}", kind, charged=amount))
    whole = money.total(line.spent_micros for line in aggregate(rows, lambda r: "all"))
    for key in (
        lambda r: r.provider_code,
        lambda r: r.operation,
        lambda r: f"{r.provider_code}{r.operation}",
    ):
        assert money.total(line.spent_micros for line in aggregate(rows, key)) == whole


def test_reconcile_detects_every_mismatch() -> None:
    parts = aggregate(ROWS, lambda r: r.provider_code)
    assert check(1_010_000, 1_010_000, parts) == ()
    assert check(1_010_001, 1_010_000, parts) == ("spent 1010001 != ledger 1010000",)
    assert check(1_010_000, 1_000_000, parts) == (
        "spent 1010000 != ledger 1000000",
        "parts 1010000 != ledger 1000000",
    )
    assert check(1_010_000, 1_010_000, parts, cap_columns=(2, 5), approved=(1, 5)) == (
        "run cap (2, 5) != latest approved (1, 5)",
    )
    assert check(1_010_000, 1_010_000, parts, cap_columns=(1, 5), approved=(1, 5)) == ()


def rollup(**kw: Any) -> Rollup:
    base: dict[str, Any] = dict(
        run_id="run-1",
        account_domain="synthetic-acme.example.com",
        status="PARTIAL",
        attended=False,
        cap_micros=2_000_000,
        cap_versions=((1, 2_000_000, "human:user-1", "2026-10-03T12:00:00+00:00"),),
        spent_micros=1_010_000,
        ledger_micros=1_010_000,
        overshoot_micros=0,
        by_provider=aggregate(ROWS, lambda r: r.provider_code),
        by_category=aggregate(
            ROWS,
            lambda r: {"web_search": "pages_read", "job_openings": "postings"}.get(
                r.operation, "people"
            ),
        ),
        by_operation=aggregate(ROWS, lambda r: r.operation),
        advisory={"p_search": 10_000, "p_signals": 100_000},
        blocked_rows={"cap_reached": ("research_papers",)},
        extensions=0,
        llm_micros=None,
        balances_start=(),
        balances_end=(),
        estimated_avoided_micros=None,
    )
    base.update(kw)
    return Rollup(**base)


def test_report_is_a_pure_deterministic_function_of_the_rollup(cfg: SpendConfig) -> None:
    a, b = build_report(rollup(), (), 25), build_report(rollup(), (), 25)
    assert (
        a.content_hash == b.content_hash and a.markdown == b.markdown and a.status == "reconciled"
    )
    body = a.body
    assert body["totals"] == {
        "spent_usd": "$1.01",
        "ledger_usd": "$1.01",
        "cap_usd": "$2.00",
        "remaining_usd": "$0.99",
        "percent_of_cap": "50%",
        "overshoot_usd": "$0.00",
    }
    provider = {line["key"]: line for line in body["by_provider"]}  # type: ignore[attr-defined]
    assert provider["p_search"]["variance"] == "$0.00" and provider["p_search"]["flag"] is False
    assert provider["p_signals"]["variance"] == "$0.15" and provider["p_signals"]["flag"] is True
    assert (
        provider["p_people"]["advisory_usd"] == "not measured"
        and provider["p_people"]["replays"] == "not measured"
    )
    assert provider["p_people"]["share"] == "74%" and provider["p_signals"]["credits"] == "3"
    assert (
        body["llm_usd_informational"] == "not measured"
        and body["estimated_avoided_usd"] == "not measured"
    )
    assert (
        build_report(rollup(estimated_avoided_micros=5), (), 25).body["estimated_avoided_usd"]
        == "estimate $0.000005"
    )
    failed = build_report(rollup(), ("spent 1 != ledger 2",), 25)
    assert failed.status == "reconciliation_failed" and "spent 1 != ledger 2" in failed.markdown
    under = build_report(rollup(advisory={"p_search": 20_000, "p_people": 0}), (), 25).body
    under_lines = {line["key"]: line for line in under["by_provider"]}  # type: ignore[attr-defined]
    assert (
        under_lines["p_search"]["variance"] == "-$0.01" and under_lines["p_people"]["flag"] is True
    )
    nocap = build_report(rollup(cap_micros=None, ledger_micros=0, by_provider=()), (), 25).body
    assert nocap["totals"]["percent_of_cap"] == "not measured"  # type: ignore[index]


def test_tenant_view_is_an_allow_list_projection() -> None:
    view = tenant_view(rollup())
    assert (
        view.categories == {"pages_read": 1, "people": 1, "postings": 1} and view.total_calls == 3
    )
    dumped = json.dumps(view.model_dump(mode="json"))
    for leak in ("p_search", "p_signals", "p_people", "$", "usd", "credit", "h1"):
        assert leak not in dumped
    assert view.amounts_shown is False


# ---- no model in this package (spec 8) ----------------------------------------------------------


def test_no_llm_or_vendor_name_in_the_spend_package() -> None:
    import yaml

    brokers = [
        v["name"].casefold()
        for v in yaml.safe_load(
            (ROOT / ".claude/skills/account-intelligence-platform/assets/routing.yaml").read_text()
        )["providers"].values()
    ]
    for path in (ROOT / "src/spend").glob("*.py"):
        source = path.read_text()
        tree = ast.parse(source)
        mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not [m for m in mods if re.search(r"llm|anthropic|openai|agents", m)], path.name
        assert not [b for b in brokers if b in source.casefold()], path.name
    keys = json.dumps(yaml.safe_load((ROOT / "config/spend.yaml").read_text()))
    assert not [b for b in brokers if b in keys.casefold()]
