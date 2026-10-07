"""WS8 operator plane (AD1 to AD4, D-66, D-70). SYNTHETIC tenants and a fake staff IdP only.

Lying scenarios: an operator route reachable without the staff plane, a tenant session used as an
operator, a support grant used by another operator, after revocation or after expiry, a read grant
used to write, a support action the tenant cannot see, a tenant overview that shows names or
content, a support role changing a tenant, a key reference read back, an operator approving their
own deletion request.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
import psycopg
import pytest

from account_intelligence.db.session import tenant_txn, untenanted_session
from account_intelligence.platform.ports import NotConfigured
from admin import flags, keys
from metering import limits
from tests.conftest import ADMIN_DSN
from tests.fakes.phase3 import FakeStaffIdp
from tests.phase3.conftest import (
    BASE,
    Browser,
    Plane,
    add_member,
    add_oidc_connection,
    new_tenant,
    uid,
)

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _db(sql: str, *params: Any) -> list[tuple[Any, ...]]:
    with psycopg.connect(ADMIN_DSN) as c:
        return c.execute(sql, params).fetchall()


def _staff(plane: Plane) -> httpx.AsyncClient:
    plane.app.state.container.tenant_plane.staff_verifier = FakeStaffIdp()
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=plane.app, raise_app_exceptions=False), base_url=BASE
    )


def _as(token: str, grant: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {token}"}
    if grant:
        h["X-Support-Grant"] = grant
    return h


async def _admin(plane: Plane, browser: Browser, name: str) -> UUID:
    t = new_tenant(name)
    slug = f"acme-{uid()}"
    add_oidc_connection(t, plane, slug)
    add_member(t, f"{slug}:root", "admin")
    await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
    return t


async def test_operator_routes_fail_closed_without_the_operator_plane(
    plane: Plane, browser: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k in ("STAFF_JWT_ISSUER", "STAFF_JWT_AUDIENCE", "STAFF_JWKS_URL"):
        monkeypatch.delenv(k, raising=False)
    await _admin(plane, browser, "ad-closed")
    for path in ("/operator/v1/tenants", "/operator/v1/view/accounts", "/operator/v1/flags"):
        unconfigured = await browser.client.get(path, headers=_as("operator:op-1"))
        assert unconfigured.status_code == 503, path
    async with _staff(plane) as staff:
        # a tenant admin's own session is not a staff principal
        assert (await browser.client.get("/operator/v1/tenants")).status_code == 401
        assert (await staff.get("/operator/v1/tenants")).status_code == 401
        assert (await staff.get("/operator/v1/tenants", headers=_as("admin:x"))).status_code == 401
        # a route outside the catalog is refused before any principal is resolved
        assert (await staff.get("/operator/v1/nope", headers=_as("operator:op-1"))).status_code in (
            403,
            404,
        )
        # a view-as route without a grant
        assert (
            await staff.get("/operator/v1/view/accounts", headers=_as("support:sup-1"))
        ).status_code == 403


async def _grant(browser: Browser, grantee: str, scope: str = "read", minutes: int = 30) -> str:
    r = await browser.call(
        "POST",
        "/v1/support/grants",
        json={
            "grantee": grantee,
            "scope": scope,
            "reason_code": "customer_request",
            "minutes": minutes,
        },
    )
    assert r.status_code == 201, r.text
    return str(r.json()["grant_id"])


async def test_support_view_as_is_read_only_audited_and_visible(
    plane: Plane, browser: Browser
) -> None:
    t = await _admin(plane, browser, "ad-view")
    other = new_tenant("ad-view-other")
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("INSERT INTO accounts (tenant_id, domain) VALUES (%s, 'mine.example')", (t,))
        c.execute(
            "INSERT INTO accounts (tenant_id, domain) VALUES (%s, 'theirs.example')", (other,)
        )
    too_long = await browser.call(
        "POST",
        "/v1/support/grants",
        json={"grantee": "sup-1", "reason_code": "customer_request", "minutes": 600},
    )
    assert too_long.status_code == 422
    unknown = await browser.call(
        "POST",
        "/v1/support/grants",
        json={"grantee": "sup-1", "reason_code": "curiosity", "minutes": 10},
    )
    assert unknown.status_code == 422
    gid = await _grant(browser, "sup-1")
    async with _staff(plane) as staff:
        view = await staff.get("/operator/v1/view/accounts", headers=_as("support:sup-1", gid))
        assert view.status_code == 200 and view.headers["X-Support-View"] == "read-only"
        assert [a["domain"] for a in view.json()["accounts"]] == ["mine.example"]
        assert (
            await staff.get("/operator/v1/view/settings", headers=_as("support:sup-1", gid))
        ).status_code == 200
        # the grant names one operator
        assert (
            await staff.get("/operator/v1/view/accounts", headers=_as("support:sup-2", gid))
        ).status_code == 403
        # a read grant cannot write
        write = await staff.post(
            f"/operator/v1/runs/{gid}/prepulled/intents",
            json={},
            headers=_as("support:sup-1", gid),
        )
        assert write.status_code == 403
        # the tenant sees every action, allowed or refused
        seen = (await browser.client.get("/v1/support/events")).json()["events"]
        assert {(e["operator"], e["outcome"]) for e in seen} == {
            ("sup-1", "ok"),
            ("sup-1", "denied"),
        }
        assert len(seen) == 3
        audit = _db(
            "SELECT action, actor_kind, outcome FROM audit_events WHERE tenant_id = %s"
            " AND support_grant_id IS NOT NULL ORDER BY seq",
            t,
        )
        assert ("support.view", "support", "ok") in audit
        assert ("support.write", "support", "denied") in audit
        # revocation stops the next request
        revoked = await browser.call("POST", f"/v1/support/grants/{gid}/revoke")
        assert revoked.status_code == 200
        assert (
            await staff.get("/operator/v1/view/accounts", headers=_as("support:sup-1", gid))
        ).status_code == 403
        assert (await browser.call("POST", f"/v1/support/grants/{gid}/revoke")).status_code == 404
        # expiry is enforced by the server
        short = await _grant(browser, "sup-1", minutes=5)
        assert (
            await staff.get("/operator/v1/view/accounts", headers=_as("support:sup-1", short))
        ).status_code == 200
        plane.clock.advance(minutes=6)
        assert (
            await staff.get("/operator/v1/view/accounts", headers=_as("support:sup-1", short))
        ).status_code == 403
    grants = (await browser.client.get("/v1/support/grants")).json()["grants"]
    assert {g["id"] for g in grants} == {gid, short}
    assert next(g for g in grants if g["id"] == gid)["revoked_at"] is not None


async def test_the_operator_plane_manages_tenants_without_reading_content(
    plane: Plane, browser: Browser
) -> None:
    t = await _admin(plane, browser, "ad-manage-SECRETNAME")
    async with _staff(plane) as staff:
        listing = await staff.get("/operator/v1/tenants", headers=_as("support:sup-1"))
        assert listing.status_code == 200
        assert "SECRETNAME" not in listing.text and "ad-manage" not in listing.text
        row = next(x for x in listing.json()["tenants"] if x["tenant_id"] == str(t))
        assert row["members"] == 1 and row["suspended"] is False
        # support staff cannot change a tenant
        body = {"reason_code": "abuse_report"}
        refused = await staff.post(
            f"/operator/v1/tenants/{t}/suspend", json=body, headers=_as("support:sup-1")
        )
        assert refused.status_code == 403
        done = await staff.post(
            f"/operator/v1/tenants/{t}/suspend", json=body, headers=_as("operator:op-1")
        )
        assert done.status_code == 200, done.text
        assert (await browser.client.get("/v1/accounts")).status_code in (401, 403)
        assert (
            await staff.post(
                f"/operator/v1/tenants/{t}/reactivate", json=body, headers=_as("operator:op-1")
            )
        ).status_code == 200
        made = await staff.post(
            "/operator/v1/tenants", json={"name": "fresh"}, headers=_as("operator:op-1")
        )
        assert made.status_code == 201
        fresh = made.json()["tenant_id"]
        assert _db("SELECT balance FROM wallets WHERE tenant_id = %s", fresh) == [(0,)]
        # two people delete: the requester cannot approve
        req = await staff.post(
            f"/operator/v1/tenants/{fresh}/deletion",
            json={"reason_code": "customer_request"},
            headers=_as("operator:op-1"),
        )
        assert req.status_code == 201, req.text
        rid = req.json()["request_id"]
        base = f"/operator/v1/tenants/{fresh}/deletion/{rid}"
        assert (
            await staff.post(f"{base}/approve", headers=_as("operator:op-2"))
        ).status_code == 409  # dry run first
        assert (
            await staff.post(f"{base}/dry-run", headers=_as("operator:op-1"))
        ).status_code == 200
        assert (
            await staff.post(f"{base}/approve", headers=_as("operator:op-1"))
        ).status_code == 409
        assert (
            await staff.post(f"{base}/approve", headers=_as("operator:op-2"))
        ).status_code == 200
        assert (
            await staff.post(f"{base}/execute", headers=_as("operator:op-2"))
        ).status_code == 200
    # every action is in the operator audit, and the tenant's own audit shows what was done to it
    actions = {a for (a,) in _db("SELECT action FROM operator_audit WHERE target = %s", str(t))}
    assert actions == {"tenant.suspend", "tenant.reactivate"}
    seen = {a for (a,) in _db("SELECT action FROM audit_events WHERE tenant_id = %s", t)}
    assert {"operator.suspend", "operator.reactivate"} <= seen


async def test_quotas_flags_and_keys_are_versioned_and_audited(
    plane: Plane, browser: Browser
) -> None:
    t = await _admin(plane, browser, "ad-quota")
    async with _staff(plane) as staff:
        q = await staff.post(
            f"/operator/v1/tenants/{t}/quotas",
            json={"dimension": "accounts", "limit_value": 12, "reason_code": "pilot_extension"},
            headers=_as("operator:op-1"),
        )
        assert q.status_code == 201 and q.json()["version"] == 1
        async with tenant_txn(t) as s:
            assert (await limits.limits(s))["accounts"] == 12
        p = await staff.post(
            f"/operator/v1/tenants/{t}/plan",
            json={"plan_code": "standard", "reason_code": "upgrade"},
            headers=_as("operator:op-1"),
        )
        assert p.status_code == 201
        async with tenant_txn(t) as s:
            got = await limits.limits(s)
        assert got["runs_per_period"] == 100 and got["accounts"] == 12  # override still wins
        assert (
            await staff.post(
                f"/operator/v1/tenants/{t}/plan",
                json={"plan_code": "gold", "reason_code": "upgrade"},
                headers=_as("operator:op-1"),
            )
        ).status_code == 422
        flag = f"flag_{uid()}".replace("-", "_").lower()
        flag = "".join(ch for ch in flag if ch.isalpha() or ch == "_")
        for rule in ({"tenants": [str(t)]}, {"percent": 0}):
            assert (
                await staff.post(
                    "/operator/v1/flags",
                    json={"flag": flag, "rule": rule, "reason_code": "rollout"},
                    headers=_as("operator:op-1"),
                )
            ).status_code == 201
        assert (
            await staff.post(
                "/operator/v1/flags",
                json={"flag": flag, "rule": {"evil": 1}, "reason_code": "rollout"},
                headers=_as("operator:op-1"),
            )
        ).status_code == 422
        async with untenanted_session() as plain:
            assert await flags.current(plain, flag) == {"tenants": [], "plans": [], "percent": 0}
        assert _db("SELECT count(*) FROM flag_events WHERE flag = %s", flag) == [(2,)]
        ref = "projects/synthetic/secrets/p_fake/versions/1"
        k = await staff.post(
            "/operator/v1/keys",
            json={"provider_code": "p_fake", "secret_ref": ref, "reason_code": "rotation"},
            headers=_as("operator:op-1"),
        )
        assert k.status_code == 201
        listed = await staff.get("/operator/v1/keys", headers=_as("operator:op-1"))
        assert listed.status_code == 200 and ref not in listed.text
        assert (await staff.get("/operator/v1/keys", headers=_as("support:s"))).status_code == 403
    async with untenanted_session() as plain:
        assert (
            await keys.effective_ref(plain, "p_fake", plane.clock.now().replace(year=2000)) is None
        )
    with pytest.raises(NotConfigured):
        keys.bring_your_own()
    seen = {a for (a,) in _db("SELECT action FROM audit_events WHERE tenant_id = %s", t)}
    assert {"operator.quota", "operator.plan"} <= seen
    ops = {
        a
        for (a,) in _db(
            "SELECT action FROM operator_audit WHERE target IN (%s, %s, 'p_fake')", str(t), flag
        )
    }
    assert ops == {"tenant.quota", "tenant.plan", "flag.set", "key.add"}


async def test_flag_evaluation_is_deny_by_default_and_stable() -> None:
    t = UUID(int=7)
    assert flags.evaluate(None, "f", t, "pilot") is False
    assert flags.evaluate({"plans": ["pilot"]}, "f", t, "pilot") is True
    assert flags.evaluate({"percent": 100}, "f", t, "x") is True
    assert flags.evaluate({"percent": 0}, "f", t, "x") is False
    assert flags.bucket("f", t) == flags.bucket("f", t)
    on = sum(flags.evaluate({"percent": 30}, "f", UUID(int=i), "x") for i in range(2000))
    assert 450 < on < 750
