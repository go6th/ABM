"""The Internal Only view (``routes.internal_view``): anonymous is 401 before any database work,
a viewer is 403 (the two-output rule: viewers never see internal material), an analyst reads a
run's internal view (its label, documents, findings and whether a file exists) and gets a 404
for the file of a run that has none, and another tenant's run is 404. SYNTHETIC data only."""

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
from account_intelligence.api.policy import policy_for
from tests.phase3.conftest import BASE, Plane, add_member, make_app, new_tenant
from tests.spend.conftest import capped_run


def test_anonymous_internal_reads_are_refused_before_any_database_work() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)
    run = uuid4()
    assert client.get(f"/v1/runs/{run}/internal").status_code == 401
    assert client.get(f"/v1/runs/{run}/internal/file").status_code == 401


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
async def test_a_viewer_never_sees_the_internal_view() -> None:
    for path in ("/v1/runs/{run_id}/internal", "/v1/runs/{run_id}/internal/file"):
        policy = policy_for("GET", path)
        assert policy is not None and "viewer" not in policy.roles, path
    tenant = new_tenant("internal-viewer")
    _, run, _ = capped_run(tenant)
    async with client(tenant_plane(tenant, "viewer", "internal-viewer")) as c:
        assert (await c.get(f"/v1/runs/{run}/internal")).status_code == 403
        assert (await c.get(f"/v1/runs/{run}/internal/file")).status_code == 403


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_an_analyst_reads_the_internal_view_of_their_tenant_only() -> None:
    tenant, other = new_tenant("internal-analyst"), new_tenant("internal-other")
    _, run, _ = capped_run(tenant)
    _, foreign, _ = capped_run(other)
    # ``capped_run`` makes the account owned by "test-owner"; rows are owner-scoped
    # (``tenant_txn`` sets app.user_id), so another user reads 404
    async with client(tenant_plane(tenant, "analyst", "test-owner")) as c:
        view = await c.get(f"/v1/runs/{run}/internal")
        assert view.status_code == 200, view.text
        body = view.json()
        assert body["run_id"] == str(run) and body["label"]
        assert body["documents"] == [] and body["findings"] == []
        assert body["public_run_id"] is None and body["file_available"] is False
        assert (await c.get(f"/v1/runs/{run}/internal/file")).status_code == 404
        # row-level security: another tenant's run does not exist here
        assert (await c.get(f"/v1/runs/{foreign}/internal")).status_code == 404
