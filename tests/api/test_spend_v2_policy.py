"""The credit manager v2 routes' refusals and one live read: anonymous callers are 401 without
any database (the guard answers first), a viewer may read the live view but not approve a pause
(403, ``require_role("admin")``), an operator-plane live read without a support grant is 403,
and a viewer reads the live view of an approved run. The approval flow itself is covered by
``test_spend_v2_routes``. SYNTHETIC data only."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from account_intelligence.api.auth import Identity, identity
from account_intelligence.api.main import create_app
from spend.config import SpendConfig, load_spend_config
from tests.fakes.phase3 import FakeStaffIdp
from tests.phase3.conftest import BASE, Plane, add_member, make_app
from tests.spend.test_connector_gate import _approved

SUPPORT = {"Authorization": "Bearer support:sup-spend"}


@pytest.fixture
def cfg() -> SpendConfig:
    return load_spend_config()


def test_anonymous_callers_are_refused_before_any_database_work() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)
    run = uuid4()
    assert client.get(f"/v1/runs/{run}/spend/live").status_code == 401
    assert client.post(f"/v1/runs/{run}/cap/pauses/{uuid4()}/approve", json={}).status_code == 401
    assert client.post(f"/v1/runs/{run}/cap/pauses/{uuid4()}/decline", json={}).status_code == 401


def tenant_plane(tenant: UUID, role: str, user: str) -> Plane:
    plane = make_app()
    add_member(tenant, user, role)
    now: datetime = plane.clock.now()

    async def ident() -> Identity:
        return Identity(user, tenant, role, now)

    plane.app.dependency_overrides[identity] = ident
    return plane


@asynccontextmanager
async def client(plane: Plane) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=plane.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_viewer_reads_the_live_view_but_cannot_approve_a_pause(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    _, run, _ = await _approved(tenant_a, cfg)
    # the approved run's account is owned by "test-owner" and rows are owner-scoped
    async with client(tenant_plane(tenant_a, "viewer", "test-owner")) as c:
        live = await c.get(f"/v1/runs/{run}/spend/live")
        assert live.status_code == 200, live.text
        body = live.json()
        assert body["run_id"] == str(run) and set(body) == {
            "run_id",
            "status",
            "calls_by_category",
            "as_of",
        }
        assert "micros" not in live.text and "$" not in live.text
        refused = await c.post(f"/v1/runs/{run}/cap/pauses/{uuid4()}/approve", json={})
        assert refused.status_code == 403


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_operator_live_view_needs_a_support_grant(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    _, run, _ = await _approved(tenant_a, cfg)
    plane = make_app()
    plane.app.state.container.tenant_plane.staff_verifier = FakeStaffIdp(now=plane.clock.now())
    async with client(plane) as c:
        assert (await c.get(f"/operator/v1/runs/{run}/spend/live")).status_code == 401
        no_grant = await c.get(f"/operator/v1/runs/{run}/spend/live", headers=SUPPORT)
        assert no_grant.status_code == 403
        bad_grant = await c.get(
            f"/operator/v1/runs/{run}/spend/live",
            headers={**SUPPORT, "X-Support-Grant": str(uuid4())},
        )
        assert bad_grant.status_code == 403
