"""CapGuard rules added in this work package, on real Postgres: a pre-pulled file replaces the paid
call at zero cost (D-31, K8); a "free" operation that charged is a defect and refused from then on
(K14); the budget node funds High rows first and blocks the rest (K10); two workers, ten parallel
runs over two tenants and racing finalisation (K4, K12); fault injection at every step leaves a
consistent state (spec 9.10); a 10,000-row ledger reports within a memory cap (spec 9.11)."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import time
import tracemalloc
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.exc import OperationalError

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.errors import (
    BudgetExceeded,
    CapRefused,
    ConnectorError,
    SchemaDriftError,
)
from account_intelligence.connectors.models import WebSearchArgs
from account_intelligence.connectors.service import call_operation
from account_intelligence.db.session import tenant_txn
from account_intelligence.workflow.calls import fund_wave
from spend import service
from spend.config import SpendConfig
from spend.gate import RunCapState, decide
from spend.models import Principal
from tests.connectors.conftest import CASSETTES, Reply, context
from tests.fakes.spend import COSTS, PLAN, STAGES, FakeBalances
from tests.spend.conftest import NOW, admin, capped_run
from tests.spend.test_connector_gate import OK, _approved, _harness

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
SEARCH = WebSearchArgs(queries=("synthetic prepulled",))


def _cassette_bytes() -> tuple[bytes, int]:
    body = json.loads((CASSETTES / "p_search" / "web_search.json").read_text())["response"]["body"]
    return json.dumps(body).encode(), len(body["results"])


async def _register(tenant: UUID, run: UUID, harness: Any, body: bytes, count: int) -> str:
    key = f"{tenant}/prepulled/{hashlib.sha256(body).hexdigest()}.json"
    await harness.payloads.put(key, body)
    return await service.register_prepulled(
        tenant_txn, tenant, run, "p_search", "web_search", body, count, "operator:u", key
    )


# ---- D-31 pre-pulled files ------------------------------------------------------------------


async def test_prepulled_file_replaces_the_paid_call_at_zero_cost(tenant_a: UUID) -> None:
    harness = _harness()
    account, run, domain = capped_run(tenant_a)  # no cap at all: a file still costs nothing
    body, count = _cassette_bytes()
    await _register(tenant_a, run, harness, body, count)
    result = await call_operation(
        Operation.WEB_SEARCH, SEARCH, context(tenant_a, account, run, domain), harness.runtime()
    )
    assert result.status == "done" and len(result.records) == count
    assert result.charged_usd == 0 and result.cost_source == "prepulled"
    assert harness.providers[ProviderCode.P_SEARCH].calls == 0  # no paid call was made
    rows = admin("SELECT cost_source, cost_usd FROM tool_calls WHERE run_id = %s", run)
    assert rows == [("prepulled", Decimal("0.000000"))]
    assert admin("SELECT spent_usd FROM account_runs WHERE id = %s", run) == [
        (Decimal("0.000000"),)
    ]
    report, _, _ = await service.close_run(tenant_txn, tenant_a, run, load_cfg(), NOW, 300)
    line = report.body["by_provider"][0]
    assert (line["paid_calls"], line["prepulled_calls"]) == (0, 1)  # counted apart (K8)


async def test_prepulled_file_that_does_not_match_its_registration_is_refused(
    tenant_a: UUID,
) -> None:
    harness = _harness()
    account, run, domain = capped_run(tenant_a)
    body, count = _cassette_bytes()
    await _register(tenant_a, run, harness, body, count + 1)  # registered count is wrong
    with pytest.raises(SchemaDriftError, match="record count"):
        await call_operation(
            Operation.WEB_SEARCH, SEARCH, context(tenant_a, account, run, domain), harness.runtime()
        )
    account2, run2, domain2 = capped_run(tenant_a)
    key = await _register(tenant_a, run2, harness, body, count)
    stored = admin("SELECT object_ref FROM prepulled_files WHERE run_id = %s", run2)[0][0]
    await harness.payloads.put(stored, body + b" ")  # bytes changed after registration
    assert key
    with pytest.raises(SchemaDriftError, match="hash"):
        await call_operation(
            Operation.WEB_SEARCH,
            SEARCH,
            context(tenant_a, account2, run2, domain2),
            harness.runtime(),
        )
    for r in (run, run2):  # the refused attempts are closed at zero cost
        assert admin("SELECT spent_usd FROM account_runs WHERE id = %s", r) == [
            (Decimal("0.000000"),)
        ]


def load_cfg() -> SpendConfig:
    from spend.config import load_spend_config

    return load_spend_config()


# ---- K14 a free operation that charges --------------------------------------------------------


async def test_gate_blocks_a_free_operation_that_charged(
    tenant_a: UUID, cfg: SpendConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = RunCapState(True, None, None, 0, 0, {}, frozenset({"p_search:web_search"}))
    free = frozenset({"p_search:web_search"})
    assert decide(state, "p_search:web_search", 0, True, free).reason == "free_defect"
    assert (
        decide(
            dataclasses.replace(state, defective_free_ops=frozenset()),
            "p_search:web_search",
            0,
            True,
            free,
        ).reason
        == "free"
    )
    # on the database: listed as free, but it returned a documented cost
    import spend.config

    marked = dataclasses.replace(cfg, free_operations=free)
    monkeypatch.setattr(spend.config, "load_spend_config", lambda *a, **k: marked)
    harness = _harness()
    harness.providers[ProviderCode.P_SEARCH].fallback = Reply.cassette("p_search", "web_search")
    account, run, domain = capped_run(tenant_a)  # no cap: a "free" call passes the gate once
    ctx = context(tenant_a, account, run, domain)
    await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, harness.runtime())
    assert admin("SELECT kind FROM spend_defects WHERE run_id = %s", run) == [
        ("free_operation_charged",)
    ]
    with pytest.raises(CapRefused) as refused:  # blocked from then on
        await call_operation(
            Operation.WEB_SEARCH, WebSearchArgs(queries=("another",)), ctx, harness.runtime()
        )
    assert refused.value.cap_reason == "free_defect"


# ---- K10 the budget node ----------------------------------------------------------------------


async def test_budget_node_funds_high_rows_first_and_blocks_the_rest(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    account, run, domain = await _approved(tenant_a, cfg)
    cap = admin("SELECT budget_usd FROM account_runs WHERE id = %s", run)[0][0]
    # leave room for exactly two $0.005 searches
    admin("UPDATE account_runs SET spent_usd = %s WHERE id = %s", cap - Decimal("0.010"), run)
    deps = SimpleNamespace(
        connectors=_harness().runtime(), txn=tenant_txn, tenant_id=tenant_a, run_id=run
    )
    calls = [
        ("research_papers", Operation.WEB_SEARCH, WebSearchArgs(queries=("optional 1",))),
        ("company_news", Operation.WEB_SEARCH, WebSearchArgs(queries=("high 1",))),
        ("competitor_moves", Operation.WEB_SEARCH, WebSearchArgs(queries=("optional 2",))),
        ("company_news", Operation.WEB_SEARCH, WebSearchArgs(queries=("high 2",))),
    ]
    funded, unfunded = await fund_wave(deps, calls)  # type: ignore[arg-type]
    assert [c[0] for c in funded] == ["company_news", "company_news"]
    assert unfunded == ("research_papers", "competitor_moves")
    everything, none = await fund_wave(deps, calls[:2])  # a batch that fits runs in plan order
    assert everything == calls[:2] and none == ()


# ---- concurrency ------------------------------------------------------------------------------


async def test_two_workers_and_ten_runs_over_two_tenants_never_exceed_their_caps(
    tenant_a: UUID, tenant_b: UUID, cfg: SpendConfig
) -> None:
    runs = [await _approved(t, cfg) for t in (tenant_a, tenant_b) for _ in range(5)]
    for _, run, _ in runs:
        admin("UPDATE account_runs SET tool_call_cap = 1000 WHERE id = %s", run)

    async def hammer(tenant: UUID, account: UUID, run: UUID, domain: str, worker: int) -> None:
        harness = _harness()
        harness.providers[ProviderCode.P_SEARCH].fallback = Reply.cassette("p_search", "web_search")
        rt = dataclasses.replace(harness.runtime(), worker_id=f"w{worker}")
        ctx = context(tenant, account, run, domain)
        for i in range(30):
            # refused by the cap or the run lease is fine: never a silent overspend
            with contextlib.suppress(BudgetExceeded, ConnectorError):
                await call_operation(
                    Operation.WEB_SEARCH, WebSearchArgs(queries=(f"w{worker} q{i}",)), ctx, rt
                )

    jobs = []
    for k, (account, run, domain) in enumerate(runs):
        tenant = tenant_a if k < 5 else tenant_b
        jobs += [
            hammer(tenant, account, run, domain, 2 * k),
            hammer(tenant, account, run, domain, 2 * k + 1),
        ]
    await asyncio.gather(*jobs)
    for _, run, _ in runs:
        spent, budget, over = admin(
            "SELECT spent_usd, budget_usd, cap_overshoot_usd FROM account_runs WHERE id = %s", run
        )[0]
        assert spent <= budget + over and over == 0


async def test_racing_finalisation_stores_one_report(tenant_a: UUID, cfg: SpendConfig) -> None:
    _, run, _ = await _approved(tenant_a, cfg)
    results = await asyncio.gather(
        *[service.close_run(tenant_txn, tenant_a, run, cfg, NOW, 300) for _ in range(5)]
    )
    assert {r[2] for r in results} == {1}
    assert admin("SELECT count(*) FROM spend_reports WHERE run_id = %s", run) == [(1,)]


# ---- fault injection --------------------------------------------------------------------------


class _Faulty:
    """A session proxy whose ``fail_at``-th ``execute`` raises a driver error."""

    def __init__(self, session: Any, fail_at: int) -> None:
        self._s, self._left = session, fail_at

    async def execute(self, *a: Any, **k: Any) -> Any:
        self._left -= 1
        if self._left == 0:
            raise OperationalError("injected", None, Exception("injected database failure"))
        return await self._s.execute(*a, **k)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._s, name)


def faulty_txn(fail_at: int) -> Any:
    @asynccontextmanager
    async def txn(tenant: UUID) -> AsyncIterator[Any]:
        async with tenant_txn(tenant) as s:
            yield _Faulty(s, fail_at)

    return txn


def _counts(run: UUID) -> tuple[Any, ...]:
    return tuple(
        admin(f"SELECT count(*) FROM {t} WHERE run_id = %s", run)[0][0]
        for t in ("run_cap_versions", "run_cap_events", "balance_snapshots", "spend_reports")
    ) + tuple(admin("SELECT budget_usd, cap_version FROM account_runs WHERE id = %s", run)[0])


@pytest.mark.parametrize("step", ["propose", "approve", "close"])
async def test_a_database_failure_at_any_step_leaves_a_consistent_state(
    tenant_a: UUID, cfg: SpendConfig, step: str
) -> None:
    account, run, domain = capped_run(tenant_a)
    human = Principal(kind="human", subject="u", tenant_id=str(tenant_a), role="admin")
    proposed = None
    if step != "propose":
        proposed = await service.propose(
            tenant_txn, tenant_a, run, PLAN, COSTS, FakeBalances(OK, NOW), cfg, NOW, STAGES
        )
    if step == "close":
        await service.approve(
            tenant_txn,
            tenant_a,
            run,
            proposed.version,  # type: ignore[union-attr]
            proposed.proposal.content_hash,
            human,
            cfg,
            NOW,
        )  # type: ignore[union-attr]
    before = _counts(run)
    for fail_at in range(1, 40):
        txn = faulty_txn(fail_at)
        try:
            if step == "propose":
                await service.propose(
                    txn, tenant_a, run, PLAN, COSTS, FakeBalances(OK, NOW), cfg, NOW, STAGES
                )
            elif step == "approve":
                assert proposed is not None
                await service.approve(
                    txn,
                    tenant_a,
                    run,
                    proposed.version,
                    proposed.proposal.content_hash,
                    human,
                    cfg,
                    NOW,
                )
            else:
                await service.close_run(txn, tenant_a, run, cfg, NOW, 300)
        except OperationalError:
            assert _counts(run) == before, (step, fail_at)  # all or nothing
            continue
        break  # the step completed without reaching the injected failure
    else:  # pragma: no cover - the loop always reaches a clean run
        raise AssertionError("the step never completed")
    assert _counts(run) != before  # and the clean run did its work once


# ---- scale smoke ------------------------------------------------------------------------------


async def test_ten_thousand_ledger_rows_report_within_a_memory_cap(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    _, run, _ = await _approved(tenant_a, cfg)
    # 5,000 done calls at $0.0002 = $1.00, within the approved cap (the cap itself is never edited)
    admin("UPDATE account_runs SET spent_usd = 1.000000 WHERE id = %s", run)
    admin(
        "INSERT INTO tool_call_events (tenant_id, run_id, args_hash, attempt, event, owner,"
        " lease_epoch, provider_code, operation, reserved_usd, charged_usd, cost_source)"
        " SELECT %s, %s, md5(g::text) || md5((g + 1)::text), 1, e, 'w', 1,"
        " CASE WHEN g %% 2 = 0 THEN 'p_search' ELSE 'p_scrape' END,"
        " CASE WHEN g %% 2 = 0 THEN 'web_search' ELSE 'scrape_job_page' END,"
        " 0.0002, CASE WHEN e = 'done' THEN 0.0002 END, 'documented'"
        " FROM generate_series(1, 5000) g, (VALUES ('started'), ('done')) v(e)",
        tenant_a,
        run,
    )
    tracemalloc.start()
    started = time.perf_counter()
    report, _, _ = await service.close_run(tenant_txn, tenant_a, run, cfg, NOW, 300)
    seconds = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert report.status == "reconciled", report.body["reconciliation"]
    assert sum(x["paid_calls"] for x in report.body["by_provider"]) == 5000
    assert peak < 512 * 1024**2  # a smoke ceiling, not a target
    print(f"\nSPEND SCALE 10000 ledger rows: {seconds:.2f} s, peak traced {peak / 1024**2:.1f} MiB")


# ---- K4 for any call sequence ------------------------------------------------------------------


async def test_any_reserve_and_true_up_sequence_keeps_spent_within_cap_plus_overshoot(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    """Seeded random sequences of the connector's own atomic reserve and true-up (the only
    writers of ``spent_usd``): ``spent <= cap + overshoot`` after every step, and the database
    CHECK never fires. (Seeded sequences rather than hypothesis: the async engine is bound to the
    test loop.)"""
    import random

    from account_intelligence.connectors.budget import BudgetDefaults, reserve, true_up

    for seed in range(20):
        rnd = random.Random(seed)
        _, run, _ = await _approved(tenant_a, cfg)
        admin("UPDATE account_runs SET tool_call_cap = 10000 WHERE id = %s", run)
        for _ in range(40):
            est = round(rnd.uniform(0, 0.02), 6)
            async with tenant_txn(tenant_a) as s:
                try:
                    await reserve(s, run, est, BudgetDefaults(5.0, 60))
                except BudgetExceeded:
                    continue
                # the actual: below, at, or (rarely) above the reserved upper bound
                await true_up(s, run, round(rnd.choice([-est, -est / 2, 0, 0, 0.003]), 6))
            spent, budget, over = admin(
                "SELECT spent_usd, budget_usd, cap_overshoot_usd FROM account_runs WHERE id = %s",
                run,
            )[0]
            assert spent <= budget + over, (seed, spent, budget, over)


# ---- K3 no agent or model path approves or extends ------------------------------------------


async def test_no_agent_or_model_path_can_approve_or_extend() -> None:
    """Only the API router (a person, identity from the verified token) and the setup adapter
    (the tenant policy within its limit) approve a cap; ``lifecycle`` only stores the report.
    No agent, tool, prompt or LLM-facing module reaches the cap service."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src"
    callers = {
        str(p.relative_to(src))
        for p in src.rglob("*.py")
        if "service.approve" in p.read_text() or "service.request_extension" in p.read_text()
    }
    assert callers == {
        "account_intelligence/api/routes/spend.py",
        "account_intelligence/workflow/stages/spend.py",
    }
    stage = (src / "account_intelligence/workflow/stages/spend.py").read_text()
    assert 'kind="tenant_policy"' in stage and 'kind="human"' not in stage
    for folder in ("account_intelligence/agents", "account_intelligence/platform"):
        for p in (src / folder).rglob("*.py"):
            assert "spend" not in p.read_text(), p
