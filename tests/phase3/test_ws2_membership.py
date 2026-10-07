"""WS2 membership rules on PostgreSQL under races (ID4), and the fail-closed tenant plane.

Lying scenario: two admins demote each other at the same moment; each sees two admins when it
starts. The row locks make the second wait and re-count, so one demotion is refused and the
tenant keeps an admin. SYNTHETIC tenants only."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from identity import membership
from tests.conftest import ADMIN_DSN
from tests.phase3.conftest import add_member, new_tenant

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


async def _demote(tenant: object, actor: str, target: str) -> str:
    async with tenant_txn(tenant) as s:  # type: ignore[arg-type]
        await membership.change(
            s, actor=actor, user_id=target, expected_version=1, now=NOW, new_role="viewer"
        )
        await asyncio.sleep(0.1)  # hold the locks while the other transaction tries
    return "demoted"


async def test_membership_changes_follow_the_role_rules_under_races() -> None:
    t = new_tenant("race")
    add_member(t, "idp:a", "admin")
    add_member(t, "idp:b", "admin")
    results = await asyncio.gather(
        _demote(t, "idp:a", "idp:b"), _demote(t, "idp:b", "idp:a"), return_exceptions=True
    )
    assert results.count("demoted") == 1, results
    refused = [r for r in results if isinstance(r, membership.MembershipRefused)]
    assert [r.reason for r in refused] == ["last_admin"]
    with psycopg.connect(ADMIN_DSN) as c:
        admins = c.execute(
            "SELECT count(*) FROM memberships WHERE tenant_id = %s AND role = 'admin'"
            " AND status = 'active'",
            (t,),
        ).fetchone()[0]
        events = c.execute(
            "SELECT event FROM membership_events WHERE tenant_id = %s", (t,)
        ).fetchall()
    assert admins == 1 and events == [("role_changed",)]


async def test_membership_conflicts_and_unknown_members() -> None:
    t = new_tenant("conflict")
    add_member(t, "idp:a", "admin")
    add_member(t, "idp:b", "seller")
    async with tenant_txn(t) as s:
        with pytest.raises(membership.MembershipConflict):
            await membership.change(
                s, actor="idp:a", user_id="idp:b", expected_version=7, now=NOW, new_role="viewer"
            )
    async with tenant_txn(t) as s:
        with pytest.raises(membership.MembershipRefused) as missing:
            await membership.change(
                s,
                actor="idp:a",
                user_id="idp:nobody",
                expected_version=1,
                now=NOW,
                new_role="viewer",
            )
        assert missing.value.reason == "not_a_member"
        removed = await membership.change(
            s, actor="idp:a", user_id="idp:b", expected_version=1, now=NOW, new_status="removed"
        )
        assert removed.status == "removed"
        with pytest.raises(membership.MembershipRefused) as gone:
            await membership.change(
                s, actor="idp:a", user_id="idp:b", expected_version=2, now=NOW, new_status="active"
            )
        assert gone.value.reason == "removed"
        with pytest.raises(membership.MembershipRefused) as twice:
            await membership.add(s, user_id="idp:a", role="viewer", actor="idp:x", now=NOW)
        assert twice.value.reason == "already_member"
        with pytest.raises(membership.MembershipRefused):
            await membership.add(s, user_id="idp:c", role="root", actor="idp:a", now=NOW)
        with pytest.raises(membership.MembershipRefused):
            await membership.add(s, user_id="idp:a2", role="viewer", actor="idp:a2", now=NOW)
        assert await membership.active_role(s, "idp:b") is None


async def test_unconfigured_tenant_plane_fails_closed() -> None:
    """Without a wired tenant plane every port raises NotConfigured and the routes answer 503:
    no sign-in can start, no session can be read, nothing pretends to work."""
    from account_intelligence.api.main import create_app
    from account_intelligence.app.container import build_container

    app = create_app()
    app.state.container = build_container({"APP_ENV": "test"})
    app.state.draining = False
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://app.test") as c:
        start = await c.get("/v1/sso/any-slug/start")
        assert start.status_code == 503
        callback = await c.get("/v1/sso/oidc/callback", params={"state": "s" * 43, "code": "c"})
        assert callback.status_code == 503
