"""Edge paths of the cap: negative costs refused, a drift between the rollup and the connector's
ledger sum stamped (K7), pending proposals expired once (K3), a report carrying Blocked rows and the
end balances (K15, K16), refusals of an unsourced or malformed config, the typed errors."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml

from account_intelligence.db.session import tenant_txn
from spend import service
from spend.config import SpendConfig, load_spend_config
from spend.errors import CapExhausted, CapNotApproved, ConfigError
from spend.priority import Candidate, fund_indexed
from tests.fakes.spend import COSTS, PLAN, STAGES, FakeBalances
from tests.spend.conftest import NOW, admin, capped_run
from tests.spend.test_connector_gate import OK, _approved

CONFIG = Path(__file__).resolve().parents[2] / "config" / "spend.yaml"


def test_negative_costs_are_refused_by_indexed_funding() -> None:
    with pytest.raises(ValueError, match="negative"):
        fund_indexed([Candidate("r", "High", 0, "web_search", -1)], 10)


def test_cap_exhausted_carries_what_remains() -> None:
    error = CapExhausted("no room", remaining_micros=1234)
    assert error.remaining_micros == 1234 and "no room" in str(error)


def _write(tmp: Path, edit: object) -> Path:
    data = yaml.safe_load(CONFIG.read_text())
    edit(data)  # type: ignore[operator]
    (tmp / "spend.yaml").write_text(yaml.safe_dump(data))
    return tmp


def test_a_credit_rate_needs_its_source_and_structure_errors_are_typed(tmp_path: Path) -> None:
    load_spend_config.cache_clear()
    with pytest.raises(ConfigError, match="needs a source"):
        load_spend_config(_write(tmp_path, lambda d: d.pop("credit_usd_rates_source")))
    load_spend_config.cache_clear()
    with pytest.raises(ConfigError, match="invalid spend configuration"):
        load_spend_config(_write(tmp_path, lambda d: d.pop("categories")))
    load_spend_config.cache_clear()


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_prepulled_registration_refuses_a_negative_count(tenant_a: UUID) -> None:
    _, run, _ = capped_run(tenant_a)
    with pytest.raises(ValueError, match="negative"):
        await service.register_prepulled(
            tenant_txn, tenant_a, run, "p_search", "web_search", b"x", -1, "operator:u", "ref"
        )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_propose_for_an_unknown_run_is_refused(tenant_a: UUID, cfg: SpendConfig) -> None:
    with pytest.raises(CapNotApproved, match="not found"):
        await service.propose(
            tenant_txn, tenant_a, uuid4(), PLAN, COSTS, FakeBalances(OK, NOW), cfg, NOW, STAGES
        )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_pending_proposals_expire_once(tenant_a: UUID, cfg: SpendConfig) -> None:
    _, run, _ = capped_run(tenant_a)
    await service.propose(
        tenant_txn, tenant_a, run, PLAN, COSTS, FakeBalances(OK, NOW), cfg, NOW, STAGES
    )
    assert await service.expire_pending(tenant_txn, tenant_a, run, cfg, NOW) == []  # not yet
    late = admin("SELECT created_at FROM run_cap_versions WHERE run_id = %s", run)[0][
        0
    ] + timedelta(minutes=cfg.approval_ttl_minutes + 1)
    assert await service.expire_pending(tenant_txn, tenant_a, run, cfg, late) == [1]
    assert await service.expire_pending(tenant_txn, tenant_a, run, cfg, late) == []  # once
    assert await service.expire_pending(tenant_txn, tenant_a, uuid4(), cfg, late) == []


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_report_lists_blocked_rows_and_end_balances(tenant_a: UUID, cfg: SpendConfig) -> None:
    from tests.tracker.world import make_world, txn

    w = make_world(tenant=tenant_a)
    async with txn(w) as tracker:
        await tracker.seed(w.run, w.tenant)
    p = await service.propose(
        tenant_txn, w.tenant, w.run, PLAN, COSTS, FakeBalances(OK, NOW), cfg, NOW, STAGES
    )
    from spend.models import Principal

    human = Principal(kind="human", subject="u", tenant_id=str(w.tenant), role="admin")
    await service.approve(
        tenant_txn, w.tenant, w.run, p.version, p.proposal.content_hash, human, cfg, NOW
    )
    admin(
        "UPDATE coverage_rows SET status = 'blocked', reason_code = 'budget_cap',"
        " proof = '{}' WHERE run_id = %s AND row_key IN ('research_papers', 'podcasts')",
        w.run,
    )
    end = await FakeBalances({"p_search": "low"}, NOW).balance("p_search")
    report, _, _ = await service.close_run(tenant_txn, w.tenant, w.run, cfg, NOW, 300, [end])
    assert report.body["blocked_rows"] == {"budget_cap": ["podcasts", "research_papers"]}
    assert report.body["balances"]["end"] == ["p_search:low"]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_drift_between_rollup_and_connector_sum_is_stamped(
    tenant_a: UUID, cfg: SpendConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import spend.reconcile

    _, run, _ = await _approved(tenant_a, cfg)
    admin(
        "INSERT INTO tool_call_events (tenant_id, run_id, args_hash, attempt, event, owner,"
        " lease_epoch, provider_code, operation, reserved_usd, charged_usd, cost_source)"
        " VALUES (%s, %s, 'h', 1, 'done', 'w', 1, 'p_search', 'web_search', 0.005, 0.005,"
        " 'documented')",
        tenant_a,
        run,
    )
    admin("UPDATE account_runs SET spent_usd = 0.005 WHERE id = %s", run)
    real = spend.reconcile.ledger_events

    async def lossy(s, run_id):  # type: ignore[no-untyped-def]
        return (await real(s, run_id))[:-1]  # a rollup that lost a row

    monkeypatch.setattr(spend.reconcile, "ledger_events", lossy)
    report, _, _ = await service.close_run(tenant_txn, tenant_a, run, cfg, NOW, 300)
    assert report.status == "reconciliation_failed"
    assert "rollup differs from the connector ledger sum" in report.body["reconciliation"]
