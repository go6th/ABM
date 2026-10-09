"""Credit manager v2 API on real Postgres: pause approval needs a fresh step-up and resumes the run;
decline resumes it; the tenant live view is allow-listed; operator routes fail closed without the
operator plane and, with an operator session, serve the spec, the target and a typed verdict."""

from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest

from account_intelligence.api.auth import Identity, identity, step_up
from account_intelligence.api.operator import OperatorIdentity, require_operator
from spend import exhaustion
from spend.config import SpendConfig, load_spend_config, load_v2_config
from spend.pause import UnconfiguredWallet
from tests.spend.conftest import NOW, admin
from tests.spend.test_connector_gate import _approved
from tests.spend.test_v2_db import GOOD, Scanner, Store, cassette, open_request

# asyncio is marked per coroutine test: a module-wide mark also tags the sync tests (warning)
pytestmark = [pytest.mark.requires_postgres]


@pytest.fixture
def cfg() -> SpendConfig:
    return load_spend_config()


class Scheduler:
    def __init__(self) -> None:
        self.enqueued: list[tuple[UUID, UUID, int]] = []

    async def enqueue_slice(self, tenant_id: UUID, run_id: UUID, slice_no: int) -> None:
        self.enqueued.append((tenant_id, run_id, slice_no))


def app_for(
    tenant: UUID,
    *,
    step: Any = NOW - timedelta(minutes=1),
    role: str = "admin",
    operator: bool = False,
    **ports: Any,
) -> tuple[Any, SimpleNamespace]:
    from account_intelligence.api.main import create_app

    admin(
        "INSERT INTO memberships (tenant_id, user_id, role) VALUES (%s, 'u1', %s)"
        " ON CONFLICT (tenant_id, user_id) DO UPDATE SET role = EXCLUDED.role",
        tenant,
        role,
    )
    container = SimpleNamespace(
        clock=SimpleNamespace(now=lambda: NOW),
        scheduler=Scheduler(),
        wallet=ports.get("wallet", UnconfiguredWallet()),
        upload_store=ports.get("upload_store", Store(cassette())),
        scanner=ports.get("scanner", Scanner(True)),
        upload_parser=ports.get("upload_parser", GOOD),
    )
    app = create_app()
    app.state.container = container
    app.state.draining = False

    async def ident() -> Identity:
        return Identity("u1", tenant, role, step)

    app.dependency_overrides[identity] = ident
    if operator:

        async def op() -> OperatorIdentity:
            return OperatorIdentity("op-1", tenant, "support-session-1", NOW)

        app.dependency_overrides[require_operator] = op
    return app, container


async def call(app: Any, method: str, path: str, **kw: Any) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(method, path, **kw)


def pause_run(run: UUID) -> None:
    admin(
        "UPDATE account_runs SET status = 'AWAITING_APPROVAL', awaiting_kind = 'cap',"
        " slices_used = 3 WHERE id = %s",
        run,
    )


def test_step_up_is_read_only_from_mfa_claims() -> None:
    assert step_up({"amr": ["pwd", "mfa"], "auth_time": 1_700_000_000}) is not None
    assert step_up({"amr": ["pwd"], "auth_time": 1_700_000_000}) is None
    assert step_up({"amr": ["mfa"], "auth_time": "1700000000"}) is None
    assert step_up({"amr": ["mfa"], "auth_time": True}) is None
    assert step_up({}) is None


@pytest.mark.asyncio
async def test_pause_approval_route_needs_step_up_and_resumes_the_run(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    _, run, _ = await _approved(tenant_a, cfg)
    request_id, _ = await open_request(tenant_a, run, cfg)
    pause_run(run)
    path = f"/v1/runs/{run}/cap/pauses/{request_id}/approve"
    no_step, _ = app_for(tenant_a, step=None)
    assert (await call(no_step, "POST", path, json={})).status_code == 403
    seller, _ = app_for(tenant_a, role="seller")
    assert (await call(seller, "POST", path, json={})).status_code == 403
    bad, _ = app_for(tenant_a)
    tenant_in_body = await call(bad, "POST", path, json={"tenant_id": str(tenant_a)})
    assert tenant_in_body.status_code == 422  # no tenant ever comes from a body
    app, container = app_for(tenant_a)
    ok = await call(app, "POST", path, json={})
    assert ok.status_code == 200 and ok.json()["resumed"] is True
    assert container.scheduler.enqueued == [(tenant_a, run, 3)]
    assert admin("SELECT status, awaiting_kind FROM account_runs WHERE id = %s", run) == [
        ("RUNNING", None)
    ]
    again = await call(app, "POST", path, json={})
    assert again.status_code == 403  # single use


@pytest.mark.asyncio
async def test_decline_route_resumes_to_end_with_partial_results(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    _, run, _ = await _approved(tenant_a, cfg)
    request_id, _ = await open_request(tenant_a, run, cfg)
    pause_run(run)
    app, container = app_for(tenant_a)
    res = await call(app, "POST", f"/v1/runs/{run}/cap/pauses/{request_id}/decline")
    assert res.status_code == 200 and res.json()["resumed"] is True
    assert admin(
        "SELECT event FROM spend_limit_events WHERE request_id = %s AND event = 'declined'",
        request_id,
    ) == [("declined",)]


@pytest.mark.asyncio
async def test_tenant_live_route_never_shows_amounts_or_providers(
    tenant_a: UUID, tenant_b: UUID, cfg: SpendConfig
) -> None:
    _, run, _ = await _approved(tenant_a, cfg)
    app, _ = app_for(tenant_a, role="viewer")
    res = await call(app, "GET", f"/v1/runs/{run}/spend/live")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"run_id", "status", "calls_by_category", "as_of"}
    assert "micros" not in res.text and "p_" not in res.text and "$" not in res.text
    other, _ = app_for(tenant_b, role="viewer")
    assert (await call(other, "GET", f"/v1/runs/{run}/spend/live")).status_code == 404


@pytest.mark.asyncio
async def test_operator_routes_fail_closed_without_the_operator_plane(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    _, run, _ = await _approved(tenant_a, cfg)
    app, _ = app_for(tenant_a)  # a tenant admin token is not an operator session
    res = await call(app, "GET", f"/operator/v1/runs/{run}/spend/live")
    assert res.status_code == 503


@pytest.mark.asyncio
async def test_operator_upload_flow_returns_spec_target_and_a_typed_verdict(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    account, run, domain = await _approved(tenant_a, cfg)
    v2 = load_v2_config()
    spec = exhaustion.file_spec(
        "p_search",
        "web_search",
        [{"args_hash": "e" * 64, "args": {"queries": ["synthetic"]}}],
        domain,
        v2,
    )
    from account_intelligence.db.session import tenant_txn

    async with tenant_txn(tenant_a) as s:
        intent = await exhaustion.request_upload(s, tenant_a, run, 1, spec, NOW, v2)
    records = json.loads(cassette())["results"]
    from tests.spend.test_v2_db import Parser

    app, _ = app_for(tenant_a, operator=True, upload_parser=Parser(records))
    target = await call(
        app, "POST", f"/operator/v1/runs/{run}/prepulled/intents", json={"intent_id": str(intent)}
    )
    assert target.status_code == 200
    assert target.json()["spec"]["pending_calls"][0]["args_hash"] == "e" * 64
    assert target.json()["upload"]["max_bytes"] == v2.upload_max_bytes
    done = await call(
        app,
        "POST",
        f"/operator/v1/runs/{run}/prepulled/{intent}/complete",
        json={"account_confirmed": True, "pull_date": str(date(2026, 10, 1))},
    )
    assert done.status_code == 200
    assert done.json()["accepted"] is True and done.json()["operations"] == ["p_search.web_search"]
    assert "results" not in done.text  # never file content
    status = await call(app, "GET", f"/operator/v1/runs/{run}/prepulled/{intent}")
    assert [e["event"] for e in status.json()["events"]][-1] == "accepted"
    live = await call(app, "GET", f"/operator/v1/runs/{run}/spend/live")
    assert live.status_code == 200 and live.json()["projection"]["label"] == "estimate"
