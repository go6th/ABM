"""The gate inside the connector entry (K2, K4, K5): ``call_operation`` and the ledger claim on
real Postgres. A paid call needs an approved cap even when the graph is bypassed; 200 parallel
paid calls never take a run past its cap; an overshoot stops further calls; an unknown-cost
operation needs a sample line."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID

import pytest

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.budget import BudgetDefaults, true_up
from account_intelligence.connectors.errors import BudgetExceeded, CapRefused
from account_intelligence.connectors.ledger import CallKey, Claimed, Ledger
from account_intelligence.connectors.models import WebSearchArgs
from account_intelligence.connectors.payloads import InMemoryPayloadStore
from account_intelligence.connectors.service import call_operation
from account_intelligence.db.session import tenant_txn
from spend import service
from spend.config import SpendConfig
from spend.models import Principal
from tests.connectors.conftest import FakeClock, FakeProvider, FakeSleep, Harness, Reply, context
from tests.fakes.spend import COSTS, PLAN, STAGES, FakeBalances
from tests.spend.conftest import NOW, admin, capped_run

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
SEARCH = WebSearchArgs(queries=("synthetic acme news",))
OK = {"p_search": "ok", "p_signals": "ok", "p_people": "ok"}


def _harness() -> Harness:
    clock = FakeClock()
    return Harness(clock=clock, sleep=FakeSleep(clock), providers={c: FakeProvider(clock) for c in ProviderCode},
                   payloads=InMemoryPayloadStore(), resolver_table={})


async def _approved(tenant: UUID, cfg: SpendConfig, **kw: object) -> tuple[UUID, UUID, str]:
    account, run, domain = capped_run(tenant, **kw)  # type: ignore[arg-type]
    p = await service.propose(tenant_txn, tenant, run, PLAN, COSTS, FakeBalances(OK, NOW), cfg, NOW, STAGES)
    human = Principal(kind="human", subject="u", tenant_id=str(tenant), role="admin")
    await service.approve(tenant_txn, tenant, run, p.version, p.proposal.content_hash, human, cfg, NOW)
    return account, run, domain


async def test_no_paid_call_without_an_approved_cap_even_direct_to_the_connector(tenant_a: UUID, cfg: SpendConfig) -> None:
    harness = _harness()
    harness.providers[ProviderCode.P_SEARCH].script(Reply.cassette("p_search", "web_search"))
    account, run, domain = capped_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    with pytest.raises(CapRefused) as caught:
        await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, harness.runtime())
    assert caught.value.cap_reason == "no_cap"
    await service.propose(tenant_txn, tenant_a, run, PLAN, COSTS, FakeBalances(OK, NOW), cfg, NOW, STAGES)
    with pytest.raises(CapRefused):  # proposed but not approved: still no paid call
        await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, harness.runtime())
    assert harness.providers[ProviderCode.P_SEARCH].calls == 0
    assert admin("SELECT count(*) FROM tool_call_events WHERE run_id = %s", run) == [(0,)]
    assert admin("SELECT spent_usd FROM account_runs WHERE id = %s", run) == [(Decimal("0.000000"),)]


async def test_an_approved_cap_lets_the_call_through_and_a_replay_is_free(tenant_a: UUID, cfg: SpendConfig) -> None:
    harness = _harness()
    harness.providers[ProviderCode.P_SEARCH].script(Reply.cassette("p_search", "web_search"))
    account, run, domain = await _approved(tenant_a, cfg)
    ctx = context(tenant_a, account, run, domain)
    first = await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, harness.runtime())
    second = await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, harness.runtime())
    assert (first.replayed, second.replayed, second.charged_usd) == (False, True, 0)
    assert harness.providers[ProviderCode.P_SEARCH].calls == 1


async def test_two_hundred_parallel_paid_calls_never_exceed_the_cap(tenant_a: UUID, cfg: SpendConfig) -> None:
    account, run, domain = await _approved(tenant_a, cfg)
    cap = admin("SELECT budget_usd FROM account_runs WHERE id = %s", run)[0][0]
    admin("UPDATE account_runs SET tool_call_cap = 1000 WHERE id = %s", run)
    ctx = context(tenant_a, account, run, domain)
    ledger = Ledger(tenant_txn, FakeClock(), lease_ttl_s=300, budget_defaults=BudgetDefaults(5.0, 60))
    keys = [CallKey(args_hash=f"{i:064x}", tool="p_search:web_search", provider_code=ProviderCode.P_SEARCH,
                    operation=Operation.WEB_SEARCH) for i in range(200)]
    outcomes = await asyncio.gather(*[ledger.claim(ctx, "w", k, 0.01) for k in keys], return_exceptions=True)
    ok = [o for o in outcomes if isinstance(o, Claimed)]
    refused = [o for o in outcomes if isinstance(o, BudgetExceeded)]
    assert len(ok) + len(refused) == 200 and ok and refused
    spent = admin("SELECT spent_usd FROM account_runs WHERE id = %s", run)[0][0]
    assert spent <= cap and spent == Decimal("0.01") * len(ok)
    assert Decimal(str(await ledger.ledger_sum(ctx))) == spent


async def test_an_overshoot_is_recorded_unclamped_and_stops_further_calls(tenant_a: UUID, cfg: SpendConfig) -> None:
    account, run, domain = await _approved(tenant_a, cfg)
    ctx = context(tenant_a, account, run, domain)
    ledger = Ledger(tenant_txn, FakeClock(), lease_ttl_s=300, budget_defaults=BudgetDefaults(5.0, 60))
    key = CallKey(args_hash="a" * 64, tool="t", provider_code=ProviderCode.P_SEARCH, operation=Operation.WEB_SEARCH)
    await ledger.claim(ctx, "w", key, 0.005)
    async with tenant_txn(tenant_a) as s:  # the actual came back above the declared upper bound
        await true_up(s, run, 0.015)
    row = admin("SELECT spent_usd, cap_overshoot_usd FROM account_runs WHERE id = %s", run)[0]
    assert row == (Decimal("0.020000"), Decimal("0.015000"))  # never clamped
    with pytest.raises(CapRefused) as caught:
        await ledger.claim(ctx, "w", CallKey(args_hash="b" * 64, tool="t", provider_code=ProviderCode.P_SEARCH,
                                            operation=Operation.WEB_SEARCH), 0.005)
    assert caught.value.cap_reason == "overshoot"


async def test_unknown_cost_needs_a_sample_line_and_is_bounded(tenant_a: UUID, cfg: SpendConfig) -> None:
    account, run, domain = await _approved(tenant_a, cfg)
    ctx = context(tenant_a, account, run, domain)
    ledger = Ledger(tenant_txn, FakeClock(), lease_ttl_s=300, budget_defaults=BudgetDefaults(5.0, 60))

    def key(i: int, code: ProviderCode, op: Operation) -> CallKey:
        return CallKey(args_hash=f"{i:064x}", tool="t", provider_code=code, operation=op)

    with pytest.raises(CapRefused) as caught:
        await ledger.claim(ctx, "w", key(1, ProviderCode.P_ORG, Operation.ORG_TEAMS), 0.25, cost_known=False)
    assert caught.value.cap_reason == "unknown_cost"
    for i in range(cfg.sample_size):  # the approved sample round
        claim = await ledger.claim(ctx, "w", key(10 + i, ProviderCode.P_PEOPLE, Operation.PROFILE_ENRICHMENT), 0.25, cost_known=False)
        assert isinstance(claim, Claimed)
    with pytest.raises(CapRefused):  # no second round without a new approval
        await ledger.claim(ctx, "w", key(99, ProviderCode.P_PEOPLE, Operation.PROFILE_ENRICHMENT), 0.25, cost_known=False)


async def test_legacy_runs_keep_the_old_budget_rule(tenant_a: UUID) -> None:
    account, run, domain = capped_run(tenant_a, cap_required=False)
    ctx = context(tenant_a, account, run, domain)
    ledger = Ledger(tenant_txn, FakeClock(), lease_ttl_s=300, budget_defaults=BudgetDefaults(5.0, 60))
    claim = await ledger.claim(ctx, "w", CallKey(args_hash="c" * 64, tool="t", provider_code=ProviderCode.P_SEARCH,
                                                 operation=Operation.WEB_SEARCH), 0.01)
    assert isinstance(claim, Claimed)
