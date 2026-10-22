"""Support view-as (``routes.operator_view``): anonymous is 401, a support member without a grant,
with an unknown grant or with a grant issued to someone else is 403; inside an active read
grant the accounts, settings and workspace views answer 200 with the read-only banner and show
only the granting tenant's accounts. SYNTHETIC data only."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from account_intelligence.api.policy import policy_for
from account_intelligence.db.session import tenant_txn
from admin import support
from tests.conftest import ADMIN_DSN
from tests.fakes.phase3 import FakeStaffIdp
from tests.phase3.conftest import BASE, Plane, add_member, make_app, new_tenant

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def staff_plane() -> Plane:
    plane = make_app()
    plane.app.state.container.tenant_plane.staff_verifier = FakeStaffIdp(now=plane.clock.now())
    return plane


def as_support(who: str, grant: UUID | str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer support:{who}"}
    if grant is not None:
        headers["X-Support-Grant"] = str(grant)
    return headers


@asynccontextmanager
async def client(plane: Plane) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=plane.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c


def add_account(tenant: UUID, domain: str) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            "INSERT INTO accounts (tenant_id, owner_user_id, domain) VALUES (%s, 'test-owner', %s)",
            (tenant, domain),
        )


async def grant_for(plane: Plane, tenant: UUID, grantee: str) -> UUID:
    async with tenant_txn(tenant) as s:
        return await support.create(
            s,
            grantee=grantee,
            scope="read",
            reason_code="customer_request",
            minutes=30,
            granted_by="tenant-admin",
            now=plane.clock.now(),
        )


async def test_view_as_needs_a_grant_issued_to_this_support_member() -> None:
    policy = policy_for("GET", "/operator/v1/view/accounts")
    assert policy is not None and policy.tenant_source == "operator_grant"
    tenant = new_tenant("view-as-refused")
    plane = staff_plane()
    grant = await grant_for(plane, tenant, "sup-view")
    async with client(plane) as c:
        assert (await c.get("/operator/v1/view/accounts")).status_code == 401
        assert (
            await c.get("/operator/v1/view/accounts", headers=as_support("sup-view"))
        ).status_code == 403
        assert (
            await c.get("/operator/v1/view/accounts", headers=as_support("sup-view", uuid4()))
        ).status_code == 403
        # the grant names one support member
        assert (
            await c.get("/operator/v1/view/accounts", headers=as_support("sup-other", grant))
        ).status_code == 403


async def test_an_active_grant_shows_the_granting_tenant_read_only() -> None:
    tenant, other = new_tenant("view-as-mine"), new_tenant("view-as-theirs")
    add_member(tenant, "view-as-admin", "admin")
    mine, theirs = f"mine-{uuid4().hex[:6]}.example", f"theirs-{uuid4().hex[:6]}.example"
    add_account(tenant, mine)
    add_account(other, theirs)
    plane = staff_plane()
    grant = await grant_for(plane, tenant, "sup-view")
    headers = as_support("sup-view", grant)
    async with client(plane) as c:
        accounts = await c.get("/operator/v1/view/accounts", headers=headers)
        assert accounts.status_code == 200, accounts.text
        assert accounts.headers["X-Support-View"] == "read-only"
        body = accounts.json()
        assert body["support_grant"] == str(grant)
        assert [a["domain"] for a in body["accounts"]] == [mine]
        for path in ("/operator/v1/view/settings", "/operator/v1/view/workspace"):
            reply = await c.get(path, headers=headers)
            assert reply.status_code == 200, (path, reply.text)
            assert reply.headers["X-Support-View"] == "read-only"
            assert theirs not in reply.text
