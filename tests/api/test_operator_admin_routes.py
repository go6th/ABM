"""The operator plane's tenant management (``/operator/v1``): anonymous is 401, support staff may
read the tenant overview but never change a tenant (403, the catalog's ``OPS`` roles), an
operator without a fresh MFA is 403; an operator reads the session and the tenant overview,
which carries counts and never tenant names. SYNTHETIC data only."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from account_intelligence.api.policy import policy_for
from tests.fakes.phase3 import FakeStaffIdp
from tests.phase3.conftest import BASE, Plane, add_member, make_app, new_tenant

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

OPERATOR = {"Authorization": "Bearer operator:op-admin"}
OPERATOR_NO_MFA = {"Authorization": "Bearer operator:op-admin:no-mfa"}
SUPPORT = {"Authorization": "Bearer support:sup-admin"}
REASON = {"reason_code": "abuse_report"}


def staff_plane() -> Plane:
    plane = make_app()
    plane.app.state.container.tenant_plane.staff_verifier = FakeStaffIdp(now=plane.clock.now())
    return plane


@asynccontextmanager
async def client(plane: Plane) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=plane.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c


async def test_tenant_changes_need_an_operator_with_fresh_mfa() -> None:
    tenant = new_tenant("op-admin-refused")
    suspend = policy_for("POST", "/operator/v1/tenants/{tenant_id}/suspend")
    assert suspend is not None and suspend.operator_roles == frozenset({"operator"})
    async with client(staff_plane()) as c:
        assert (await c.get("/operator/v1/tenants")).status_code == 401
        assert (await c.post("/operator/v1/tenants", json={"name": "x"})).status_code == 401
        refused = await c.post(
            f"/operator/v1/tenants/{tenant}/suspend", json=REASON, headers=SUPPORT
        )
        assert refused.status_code == 403
        assert (await c.get("/operator/v1/flags", headers=SUPPORT)).status_code == 403
        stale = await c.post(
            f"/operator/v1/tenants/{tenant}/suspend", json=REASON, headers=OPERATOR_NO_MFA
        )
        assert stale.status_code == 403


async def test_an_operator_reads_the_session_and_the_tenant_overview() -> None:
    tenant = new_tenant("op-admin-SECRETNAME")
    add_member(tenant, "op-admin-member", "admin")
    async with client(staff_plane()) as c:
        session = await c.get("/operator/v1/session", headers=OPERATOR)
        assert session.status_code == 200, session.text
        assert session.json() == {"staff_id": "op-admin", "role": "operator", "mfa_fresh": True}
        listing = await c.get("/operator/v1/tenants", headers=OPERATOR)
        assert listing.status_code == 200, listing.text
        assert "SECRETNAME" not in listing.text
        row = next(x for x in listing.json()["tenants"] if x["tenant_id"] == str(tenant))
        assert row["members"] == 1 and row["suspended"] is False
        # support staff read the same overview (``STAFF`` roles on the read route)
        assert (await c.get("/operator/v1/tenants", headers=SUPPORT)).status_code == 200
