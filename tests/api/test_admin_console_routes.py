"""The admin console routes (``/operator/v1/console``): the catalog admits operators only, so an
anonymous caller is 401, support staff and an operator without a fresh MFA are 403; an operator
reads the overview and the client list (counts and names, never content). SYNTHETIC data only."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from account_intelligence.api.policy import policy_for
from tests.fakes.phase3 import FakeStaffIdp
from tests.phase3.conftest import BASE, Plane, add_member, make_app, new_tenant

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

OPERATOR = {"Authorization": "Bearer operator:op-console"}
OPERATOR_NO_MFA = {"Authorization": "Bearer operator:op-console:no-mfa"}
SUPPORT = {"Authorization": "Bearer support:sup-console"}


def staff_plane() -> Plane:
    plane = make_app()
    plane.app.state.container.tenant_plane.staff_verifier = FakeStaffIdp(now=plane.clock.now())
    return plane


@asynccontextmanager
async def client(plane: Plane) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=plane.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c


async def test_the_console_refuses_anonymous_support_and_stale_mfa_callers() -> None:
    policy = policy_for("GET", "/operator/v1/console/overview")
    assert policy is not None and policy.operator_roles == frozenset({"operator"})
    async with client(staff_plane()) as c:
        assert (await c.get("/operator/v1/console/overview")).status_code == 401
        assert (await c.get("/operator/v1/console/overview", headers=SUPPORT)).status_code == 403
        assert (
            await c.get("/operator/v1/console/clients", headers=OPERATOR_NO_MFA)
        ).status_code == 403
        # a tenant admin's own JWT is not a staff token
        assert (
            await c.get("/operator/v1/console/users", headers={"Authorization": "Bearer admin:x"})
        ).status_code == 401


async def test_an_operator_reads_the_overview_and_the_client_list() -> None:
    tenant = new_tenant("console-happy")
    add_member(tenant, "console-analyst", "analyst")
    async with client(staff_plane()) as c:
        overview = await c.get("/operator/v1/console/overview", headers=OPERATOR)
        assert overview.status_code == 200, overview.text
        body: dict[str, Any] = overview.json()
        assert {"questions", "weekly", "categories"} <= set(body)
        clients = await c.get("/operator/v1/console/clients", headers=OPERATOR)
        assert clients.status_code == 200, clients.text
        row = next(x for x in clients.json()["clients"] if x["tenant_id"] == str(tenant))
        assert row["users"] == 1 and row["name"]
        detail = await c.get(f"/operator/v1/console/clients/{tenant}", headers=OPERATOR)
        assert detail.status_code == 200, detail.text
