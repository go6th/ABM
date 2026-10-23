"""Teammates on PostgreSQL (SYNTHETIC tenants and people): an admin invites a new address and the
invited person accepts the link and signs in; an address that already has an account is added
without a link (an invite link sets the password, so none is ever made for an existing identity);
an address whose invite is still waiting is refused; the team list shows who has not accepted.

GUARANTEE(test_a_new_teammate_accepts_the_invite_and_signs_in)
GUARANTEE(test_an_existing_account_is_added_without_a_link)
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import pytest

from tests.phase3.conftest import BASE, Browser, add_member, make_app, new_tenant
from tests.replica.conftest import _call

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

PASSWORD = "Long-unique-amber-credential-8812"


def _admin_plane(tenant: UUID) -> Any:
    from account_intelligence.api.auth import Identity, identity

    user = f"local:{uuid4()}"
    add_member(tenant, user, "admin")
    plane = make_app()
    now = plane.app.state.container.clock.now()  # a fresh MFA step-up on the app's own clock

    async def ident() -> Identity:
        return Identity(user, tenant, "admin", now, amr=("pwd", "mfa"))

    plane.app.dependency_overrides[identity] = ident
    return plane


async def _invite(plane: Any, email: str, role: str = "analyst") -> httpx.Response:
    return await _call(plane, "POST", "/v1/users/invite", json={"email": email, "role": role})


async def _accept(plane: Any, url: str) -> None:
    browser = Browser(plane)
    try:
        token = urlsplit(url).fragment
        done = await browser.client.post(
            "/v1/auth/invite/accept",
            json={"token": token, "password": PASSWORD},
            headers={"Origin": BASE},
        )
        assert done.status_code == 200, done.text
    finally:
        await browser.close()


async def _team(plane: Any) -> list[dict[str, Any]]:
    reply = await _call(plane, "GET", "/v1/users/team")
    assert reply.status_code == 200, reply.text
    members: list[dict[str, Any]] = reply.json()["members"]
    return members


async def test_a_new_teammate_accepts_the_invite_and_signs_in() -> None:
    plane = _admin_plane(new_tenant("team-new"))
    email = f"mate-{uuid4().hex[:8]}@harbor.example"
    made = await _invite(plane, email, "seller")
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["existing_account"] is False and body["role"] == "seller"
    url = urlsplit(body["invite_url"])
    assert url.path == "/invite" and url.fragment
    mate = next(m for m in await _team(plane) if m["email"] == email)
    assert (mate["role"], mate["pending"], mate["you"]) == ("seller", True, False)

    await _accept(plane, body["invite_url"])
    browser = Browser(plane)
    try:
        login = await browser.client.post(
            "/v1/auth/login", json={"email": email, "password": PASSWORD}, headers={"Origin": BASE}
        )
        assert login.status_code == 200, login.text
    finally:
        await browser.close()
    mate = next(m for m in await _team(plane) if m["email"] == email)
    assert mate["pending"] is False


async def test_an_existing_account_is_added_without_a_link() -> None:
    first = _admin_plane(new_tenant("team-first"))
    email = f"known-{uuid4().hex[:8]}@harbor.example"
    made = (await _invite(first, email)).json()
    await _accept(first, made["invite_url"])

    second = _admin_plane(new_tenant("team-second"))
    added = await _invite(second, email, "viewer")
    assert added.status_code == 201, added.text
    body = added.json()
    assert body["existing_account"] is True
    assert body["invite_url"] is None and body["expires_at"] is None and body["emailed"] is False
    assert any(m["email"] == email and not m["pending"] for m in await _team(second))
    # the person keeps their own password: signing in still works with it
    browser = Browser(second)
    try:
        login = await browser.client.post(
            "/v1/auth/login", json={"email": email, "password": PASSWORD}, headers={"Origin": BASE}
        )
        assert login.status_code in (200, 409), login.text  # 409: choose one of two workspaces
    finally:
        await browser.close()


async def test_an_invite_still_waiting_is_refused_elsewhere() -> None:
    first = _admin_plane(new_tenant("team-wait-a"))
    email = f"wait-{uuid4().hex[:8]}@harbor.example"
    first_reply = await _invite(first, email)
    assert first_reply.status_code == 201, first_reply.text
    second = _admin_plane(new_tenant("team-wait-b"))
    refused = await _invite(second, email)
    assert refused.status_code == 409, refused.text
    assert refused.json()["type"].endswith("invite_pending_elsewhere")
    assert all(m["email"] != email for m in await _team(second))


async def test_inviting_a_current_member_again_is_refused() -> None:
    plane = _admin_plane(new_tenant("team-twice"))
    email = f"twice-{uuid4().hex[:8]}@harbor.example"
    made = (await _invite(plane, email)).json()
    await _accept(plane, made["invite_url"])
    again = await _invite(plane, email)
    assert again.status_code == 409, again.text


async def test_an_operator_invites_one_more_user_for_a_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.fakes.phase3 import FakeStaffIdp
    from tests.phase3.conftest import BASE as APP_BASE

    for k, v in (
        ("STAFF_JWT_ISSUER", "https://staff.example/"),
        ("STAFF_JWT_AUDIENCE", "staff"),
        ("STAFF_JWKS_URL", "https://staff.example/jwks"),
        ("STAFF_OPERATOR_EMAILS", "admin@6th.example"),
        ("STAFF_SUPPORT_EMAILS", "dev@6th.example"),
    ):
        monkeypatch.setenv(k, v)
    tenant = new_tenant("team-operator")
    plane = make_app()
    plane.app.state.container.tenant_plane.staff_verifier = FakeStaffIdp(
        now=plane.app.state.container.clock.now()
    )
    email = f"op-{uuid4().hex[:8]}@harbor.example"
    transport = httpx.ASGITransport(app=plane.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=APP_BASE) as c:
        made = await c.post(
            f"/operator/v1/console/clients/{tenant}/users",
            json={"email": email, "role": "viewer"},
            headers={"Authorization": "Bearer operator:op-console"},
        )
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["role"] == "viewer" and body["existing_account"] is False
    assert urlsplit(body["invite_url"]).path == "/invite"
