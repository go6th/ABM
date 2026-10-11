"""``GET /v1/runs/{id}/cap`` on real Postgres: an approver reads the version and hash the approval
binds to, the body names no provider or operation (D-30), approving with exactly those values
resumes a run waiting at the cap gate, and another tenant sees nothing.

Before this route existed a capped run could not leave AWAITING_APPROVAL through the API: the
proposal was stored but the approve body needs its hash."""

from __future__ import annotations

import json
from uuid import UUID

import pytest

from account_intelligence.db.session import tenant_txn
from spend import service
from spend.config import SpendConfig, load_spend_config
from tests.api.test_spend_v2_routes import app_for, call
from tests.fakes.spend import COSTS, PLAN, STAGES, FakeBalances
from tests.spend.conftest import NOW, admin, capped_run

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

OK = {"p_search": "ok", "p_signals": "ok", "p_people": "ok"}


@pytest.fixture
def cfg() -> SpendConfig:
    return load_spend_config()


async def _proposed(tenant: UUID, cfg: SpendConfig) -> UUID:
    _, run, _ = capped_run(tenant)
    admin(
        "UPDATE account_runs SET status = 'AWAITING_APPROVAL', awaiting_kind = 'cap' WHERE id = %s",
        run,
    )
    await service.propose(
        tenant_txn, tenant, run, PLAN, COSTS, FakeBalances(OK, NOW), cfg, NOW, STAGES
    )
    return run


async def test_read_then_approve_resumes_the_run(tenant_a: UUID, cfg: SpendConfig) -> None:
    run = await _proposed(tenant_a, cfg)
    app, container = app_for(tenant_a, role="admin")

    got = await call(app, "GET", f"/v1/runs/{run}/cap")
    assert got.status_code == 200
    view = got.json()
    assert view["version"] == 1 and view["state"] == "proposed"
    assert len(view["proposal_hash"]) == 64 and view["cap_micros"] > 0
    assert sum(view["calls_by_category"].values()) > 0
    assert set(view) == {
        "run_id", "version", "proposal_hash", "state", "cap_micros", "calls_by_category",
        "has_exclusions", "has_unknown_costs", "is_extension", "proposed_at",
    }  # fmt: skip
    raw = json.dumps(view)
    assert not any(code in raw for code in ("p_search", "p_signals", "p_people", "p_org"))

    approved = await call(
        app,
        "POST",
        f"/v1/runs/{run}/cap/approve",
        json={"version": view["version"], "proposal_hash": view["proposal_hash"]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["resumed"] is True
    assert admin("SELECT status FROM account_runs WHERE id = %s", run) == [("RUNNING",)]
    assert [r for (_, r, _) in container.scheduler.enqueued] == [run]

    after = (await call(app, "GET", f"/v1/runs/{run}/cap")).json()
    assert after["state"] == "approved" and after["proposal_hash"] == view["proposal_hash"]


@pytest.mark.parametrize("role", ["seller", "viewer"])
async def test_non_admin_may_read_but_not_approve(
    tenant_a: UUID, cfg: SpendConfig, role: str
) -> None:
    run = await _proposed(tenant_a, cfg)
    app, _ = app_for(tenant_a, role=role)
    got = await call(app, "GET", f"/v1/runs/{run}/cap")
    assert got.status_code == 200
    view = got.json()
    refused = await call(
        app,
        "POST",
        f"/v1/runs/{run}/cap/approve",
        json={"version": view["version"], "proposal_hash": view["proposal_hash"]},
    )
    assert refused.status_code == 403


async def test_other_tenant_and_unproposed_run_get_404(
    tenant_a: UUID, tenant_b: UUID, cfg: SpendConfig
) -> None:
    run = await _proposed(tenant_a, cfg)
    other, _ = app_for(tenant_b, role="admin")
    assert (await call(other, "GET", f"/v1/runs/{run}/cap")).status_code == 404

    _, bare, _ = capped_run(tenant_a)
    own, _ = app_for(tenant_a, role="admin")
    assert (await call(own, "GET", f"/v1/runs/{bare}/cap")).status_code == 404
