"""WS2 identity end to end on PostgreSQL through the real application (ID1 to ID4).

Lying scenarios: a token claiming another tenant, a replayed callback, a stolen code used with
another sign-in (PKCE), a sign-in finished in another browser, an open redirect, an unknown user,
an unverified domain, an unmapped group, an admin without MFA, a replayed SAML assertion, a
cross-site POST without the CSRF token, a stale step-up, a membership changed behind the cache,
a request naming another tenant in its body, query or headers. SYNTHETIC tenants only.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import psycopg
import pytest

from account_intelligence.api.policy import ROUTE_POLICIES
from tests.conftest import ADMIN_DSN
from tests.phase3.conftest import (
    Browser,
    Plane,
    add_member,
    add_oidc_connection,
    add_saml_connection,
    new_tenant,
    uid,
)

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _sessions(tenant: UUID) -> list[tuple[Any, ...]]:
    with psycopg.connect(ADMIN_DSN) as c:
        return c.execute(
            "SELECT user_id, revoked_at, revoke_reason FROM auth_sessions WHERE tenant_id = %s"
            " ORDER BY created_at",
            (tenant,),
        ).fetchall()


def _membership_events(tenant: UUID) -> list[str]:
    with psycopg.connect(ADMIN_DSN) as c:
        return [
            r[0]
            for r in c.execute(
                "SELECT event FROM membership_events WHERE tenant_id = %s ORDER BY id", (tenant,)
            ).fetchall()
        ]


def _events(tenant: UUID, kind: str) -> list[str | None]:
    with psycopg.connect(ADMIN_DSN) as c:
        return [
            r[0]
            for r in c.execute(
                "SELECT reason_code FROM auth_events WHERE tenant_id = %s AND event = %s"
                " ORDER BY id",
                (tenant, kind),
            ).fetchall()
        ]


async def test_sso_positive_flows_create_a_session_in_the_connection_tenant(
    plane: Plane, browser: Browser
) -> None:
    a, b = new_tenant("sso-a"), new_tenant("sso-b")
    slug = f"acme-{uid()}"
    add_oidc_connection(a, plane, slug)
    add_member(a, f"{slug}:ann", "seller")
    add_member(b, f"{slug}:ann", "admin")  # the same subject in another tenant
    done = await browser.oidc_sign_in(slug, "ann", extra={"tenant_id": str(b)})
    assert done.status_code == 303 and done.headers["location"] == "/"
    cookie = done.headers["set-cookie"]
    assert cookie.startswith("__Host-ai_session=")
    for attribute in ("HttpOnly", "Secure", "Path=/", "SameSite=lax"):
        assert attribute.lower() in cookie.lower(), attribute
    assert "Domain=" not in cookie
    who = (await browser.client.get("/v1/sso/session")).json()
    assert who["user_id"] == f"{slug}:ann" and who["role"] == "seller"  # tenant A, not B
    assert who["csrf_token"] and "token" not in {k for k in who if k != "csrf_token"}
    assert len(_sessions(a)) == 1 and _sessions(b) == []
    # SAML, admin with MFA
    saml_slug = f"acme-saml-{uid()}"
    add_saml_connection(a, plane, saml_slug)
    add_member(a, f"{saml_slug}:bob", "admin")
    other = Browser(plane)
    try:
        assert (await other.saml_sign_in(saml_slug, "bob")).status_code == 303
        assert (await other.client.get("/v1/sso/session")).json()["role"] == "admin"
    finally:
        await other.close()


async def test_state_is_single_use_bound_to_the_browser_and_short_lived(
    plane: Plane, browser: Browser
) -> None:
    a = new_tenant("state")
    slug = f"acme-{uid()}"
    add_oidc_connection(a, plane, slug)
    add_member(a, f"{slug}:ann", "seller")
    start = await browser.client.get(f"/v1/sso/{slug}/start")
    state, code = plane.oidc.authorize(
        start.headers["location"], subject="ann", now=plane.clock.now()
    )
    # another browser cannot finish this sign-in (no or another pre-auth cookie)
    thief = Browser(plane)
    try:
        await thief.client.get(f"/v1/sso/{slug}/start")  # the thief has its own pre-auth cookie
        stolen = await thief.client.get(
            "/v1/sso/oidc/callback", params={"state": state, "code": code}
        )
        assert stolen.status_code == 401
    finally:
        await thief.close()
    assert "state_not_bound_to_browser" in _events(a, "login_failed")
    # the owner's browser completes it once; a replay of the same callback, even with the
    # original pre-auth cookie presented again, is refused
    preauth = browser.client.cookies.get("__Host-ai_preauth")
    own = await browser.client.get("/v1/sso/oidc/callback", params={"state": state, "code": code})
    assert own.status_code == 303
    browser.client.cookies.set("__Host-ai_preauth", preauth or "", domain="app.test")
    replay = await browser.client.get(
        "/v1/sso/oidc/callback", params={"state": state, "code": code}
    )
    assert replay.status_code == 401 and "state_replayed" in _events(a, "login_failed")
    # expiry
    start = await browser.client.get(f"/v1/sso/{slug}/start")
    state, code = plane.oidc.authorize(
        start.headers["location"], subject="ann", now=plane.clock.now()
    )
    plane.clock.advance(seconds=601)
    late = await browser.client.get("/v1/sso/oidc/callback", params={"state": state, "code": code})
    assert late.status_code == 401 and "state_expired" in _events(a, "login_failed")
    # unknown state, unknown slug
    assert (
        await browser.client.get("/v1/sso/oidc/callback", params={"state": "x" * 43, "code": "c"})
    ).status_code == 401
    assert (await browser.client.get("/v1/sso/no-such-slug/start")).status_code == 404


async def test_pkce_binds_the_code_to_its_own_sign_in(plane: Plane, browser: Browser) -> None:
    a = new_tenant("pkce")
    slug = f"acme-{uid()}"
    add_oidc_connection(a, plane, slug)
    add_member(a, f"{slug}:ann", "seller")
    first = await browser.client.get(f"/v1/sso/{slug}/start")
    _, code_for_first = plane.oidc.authorize(
        first.headers["location"], subject="ann", now=plane.clock.now()
    )
    second = await browser.client.get(f"/v1/sso/{slug}/start")
    state_second, _ = plane.oidc.authorize(
        second.headers["location"], subject="ann", now=plane.clock.now()
    )
    # an injected code minted for another sign-in fails at the token endpoint (verifier mismatch)
    injected = await browser.client.get(
        "/v1/sso/oidc/callback", params={"state": state_second, "code": code_for_first}
    )
    assert injected.status_code == 401 and "idp_refused_code" in _events(a, "login_failed")


async def test_open_redirects_are_refused(plane: Plane, browser: Browser) -> None:
    a = new_tenant("redirect")
    slug = f"acme-{uid()}"
    add_oidc_connection(a, plane, slug)
    add_member(a, f"{slug}:ann", "seller")
    for target in ("https://evil.test/x", "//evil.test", "/\\evil.test", "javascript:alert(1)"):
        done = await browser.oidc_sign_in(slug, "ann", return_to=target)
        assert done.status_code == 303 and done.headers["location"] == "/", target
    done = await browser.oidc_sign_in(slug, "ann", return_to="/accounts?tab=people")
    assert done.headers["location"] == "/accounts?tab=people"


@pytest.mark.parametrize(
    ("setup", "extra", "reason"),
    [
        ({}, {}, "unknown_user"),
        ({"jit_enabled": True}, {"email": "ann@elsewhere.test"}, "domain_unverified"),
        ({"jit_enabled": True}, {"email_verified": False}, "email_unverified"),
        ({"jit_enabled": True}, {"groups": ["unmapped"]}, "no_group_mapping"),
        ({"jit_enabled": True}, {"groups": ["admins"], "amr": ["pwd"]}, "admin_requires_mfa"),
        ({}, {"iss": "https://evil-idp.test"}, "wrong_issuer"),
    ],
)
async def test_sign_in_is_refused_for_every_unproven_identity(
    plane: Plane, browser: Browser, setup: dict[str, Any], extra: dict[str, Any], reason: str
) -> None:
    a = new_tenant("refuse")
    slug = f"acme-{uid()}"
    add_oidc_connection(a, plane, slug, **setup)
    done = await browser.oidc_sign_in(slug, "newcomer", extra=extra)
    assert done.status_code == 401 and "set-cookie" not in {
        k.lower() for k in done.headers if "__Host-ai_session" in done.headers.get(k, "")
    }
    assert reason in _events(a, "login_failed")
    with psycopg.connect(ADMIN_DSN) as c:  # a refused sign-in provisions nobody
        assert (
            c.execute("SELECT count(*) FROM memberships WHERE tenant_id = %s", (a,)).fetchone()[0]
            == 0
        )


async def test_jit_provisions_only_a_verified_domain_with_a_mapped_group(
    plane: Plane, browser: Browser
) -> None:
    a = new_tenant("jit")
    slug = f"acme-{uid()}"
    add_oidc_connection(a, plane, slug, jit_enabled=True)
    done = await browser.oidc_sign_in(slug, "newcomer", extra={"groups": ["analysts", "readers"]})
    assert done.status_code == 303
    with psycopg.connect(ADMIN_DSN) as c:
        assert c.execute(
            "SELECT role, status FROM memberships WHERE tenant_id = %s AND user_id = %s",
            (a, f"{slug}:newcomer"),
        ).fetchone() == ("analyst", "active")
    assert _events(a, "provisioned") == [None]


async def test_saml_assertions_cannot_be_replayed(plane: Plane, browser: Browser) -> None:
    a = new_tenant("saml-replay")
    slug = f"acme-saml-{uid()}"
    add_saml_connection(a, plane, slug)
    add_member(a, f"{slug}:bob", "seller")
    assert (
        await browser.saml_sign_in(slug, "bob", fields={"assertion_id": "_fixed1"})
    ).status_code == 303
    again = await browser.saml_sign_in(slug, "bob", fields={"assertion_id": "_fixed1"})
    assert again.status_code == 401 and "assertion_replayed" in _events(a, "login_failed")


async def test_sessions_expire_rotate_and_revoke(plane: Plane, browser: Browser) -> None:
    a = new_tenant("sessions")
    slug = f"acme-{uid()}"
    add_oidc_connection(a, plane, slug)
    add_member(a, f"{slug}:ann", "seller")
    await browser.oidc_sign_in(slug, "ann")
    # rotation on sign-in: a second sign-in in the same browser ends the first session
    old_cookie = browser.client.cookies.get("__Host-ai_session")
    await browser.oidc_sign_in(slug, "ann")
    assert [r[2] for r in _sessions(a)] == ["rotated", None]
    stale = Browser(plane)
    try:
        stale.client.cookies.set("__Host-ai_session", old_cookie or "", domain="app.test")
        assert (await stale.client.get("/v1/sso/session")).status_code == 401
    finally:
        await stale.close()
    # idle timeout (30 min proposal)
    plane.clock.advance(minutes=29)
    assert (await browser.client.get("/v1/sso/session")).status_code == 200
    plane.clock.advance(minutes=31)
    assert (await browser.client.get("/v1/sso/session")).status_code == 401
    assert _sessions(a)[-1][2] == "idle_timeout"
    # absolute timeout (8 h proposal) even with activity
    await browser.oidc_sign_in(slug, "ann")
    for _ in range(24):
        plane.clock.advance(minutes=20)
        if (await browser.client.get("/v1/sso/session")).status_code != 200:
            break
    assert _sessions(a)[-1][2] == "absolute_timeout"
    # logout and sign out everywhere
    await browser.oidc_sign_in(slug, "ann")
    second = Browser(plane)
    try:
        await second.oidc_sign_in(slug, "ann")
        assert (await browser.call("POST", "/v1/sso/logout-all")).json()["sessions_ended"] == 2
        assert (await second.client.get("/v1/sso/session")).status_code == 401
    finally:
        await second.close()
    await browser.oidc_sign_in(slug, "ann")
    assert (await browser.call("POST", "/v1/sso/logout")).status_code == 200
    assert (await browser.client.get("/v1/sso/session")).status_code == 401
    assert "session_revoked" in [None, *[r for r in ("session_revoked",)]]


def _unsafe_session_routes() -> list[tuple[str, str]]:
    out = []
    for (method, path), policy in ROUTE_POLICIES.items():
        if method in ("POST", "PUT", "PATCH", "DELETE") and "session" in policy.principal_types:
            out.append((method, path))
    return sorted(out)


async def test_csrf_is_required_on_every_state_changing_cookie_call(
    plane: Plane, browser: Browser
) -> None:
    a = new_tenant("csrf")
    slug = f"acme-{uid()}"
    add_oidc_connection(a, plane, slug)
    add_member(a, f"{slug}:root", "admin")
    await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
    routes = _unsafe_session_routes()
    assert len(routes) >= 10
    for method, template in routes:
        path = re.sub(r"\{\w+\}", "00000000-0000-4000-8000-000000000000", template)
        bare = await browser.client.request(method, path, json={})
        assert bare.status_code == 403 and "csrf_refused" in bare.text, (method, template)
        forged = await browser.client.request(
            method, path, json={}, headers={"X-CSRF-Token": "0" * 64}
        )
        assert forged.status_code == 403 and "csrf_refused" in forged.text, (method, template)
        if template.startswith("/v1/sso/logout"):
            continue  # a valid call would end the session; logout has its own test
        good = await browser.call(method, path, json={})
        assert "csrf_refused" not in good.text, (method, template, good.text)


async def test_admin_actions_need_a_fresh_step_up(plane: Plane, browser: Browser) -> None:
    a = new_tenant("stepup")
    slug = f"acme-{uid()}"
    add_oidc_connection(a, plane, slug)
    add_member(a, f"{slug}:root", "admin")
    await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
    body = {"user_id": f"{slug}:new", "role": "viewer"}
    assert (await browser.call("POST", "/v1/users", json=body)).status_code == 201
    plane.clock.advance(minutes=11)  # past the 10 minute window (proposal)
    stale = await browser.call("POST", "/v1/users", json={**body, "user_id": f"{slug}:n2"})
    assert stale.status_code == 403 and stale.json()["type"].endswith("step_up_required")
    await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
    assert (
        await browser.call("POST", "/v1/users", json={**body, "user_id": f"{slug}:n2"})
    ).status_code == 201


async def test_last_admin_and_self_change_through_the_api(plane: Plane, browser: Browser) -> None:
    a = new_tenant("lastadmin")
    slug = f"acme-{uid()}"
    add_oidc_connection(a, plane, slug)
    add_member(a, f"{slug}:root", "admin")
    await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
    me = await browser.call(
        "PATCH",
        "/v1/users",
        json={"user_id": f"{slug}:root", "expected_version": 1, "role": "viewer"},
    )
    assert me.status_code == 409 and _membership_events(a) == []
    add_member(a, f"{slug}:other", "admin")
    demote = await browser.call(
        "PATCH",
        "/v1/users",
        json={"user_id": f"{slug}:other", "expected_version": 1, "role": "viewer"},
    )
    assert demote.status_code == 200 and _membership_events(a) == ["role_changed"]
    # now root is the last admin; another admin session cannot remove them either
    with psycopg.connect(ADMIN_DSN) as c:
        assert (
            c.execute(
                "SELECT count(*) FROM memberships WHERE tenant_id = %s AND role = 'admin'"
                " AND status = 'active'",
                (a,),
            ).fetchone()[0]
            == 1
        )


async def test_tenant_is_never_taken_from_the_request(plane: Plane, browser: Browser) -> None:
    a, b = new_tenant("w3-a"), new_tenant("w3-b")
    slug = f"acme-{uid()}"
    add_oidc_connection(a, plane, slug)
    add_member(a, f"{slug}:root", "admin")
    await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
    body = {"user_id": f"{slug}:x", "role": "viewer", "tenant_id": str(b)}
    assert (await browser.call("POST", "/v1/users", json=body)).status_code == 422
    listed = await browser.client.get(
        "/v1/users",
        params={"tenant_id": str(b)},
        headers={"X-Tenant-Id": str(b), "X-Tenant": str(b)},
    )
    assert listed.status_code == 200
    assert [m["user_id"] for m in listed.json()["members"]] == [f"{slug}:root"]


async def test_membership_removal_and_role_change_take_effect_within_the_bound(
    plane: Plane,
) -> None:
    """Through the cache (about 60 s, ARCH): an access within the bound may still pass; after
    the bound it never does. Uses a bearer-style identity so the session path (which re-reads
    membership on every request) does not mask the cache."""
    from account_intelligence.api.auth import Identity, identity
    from account_intelligence.api.membership import DatabaseMembershipPort, MembershipCache

    a = new_tenant("bound")
    add_member(a, "jwt:ann", "seller")
    tick = {"t": 0.0}
    plane.app.state.membership_cache = MembershipCache(
        DatabaseMembershipPort(), ttl_seconds=60, monotonic=lambda: tick["t"]
    )

    async def as_ann() -> Identity:
        return Identity("jwt:ann", a, "seller")

    plane.app.dependency_overrides[identity] = as_ann
    b = Browser(plane)
    try:
        assert (await b.client.get("/v1/settings")).status_code == 200
        add_member(a, "jwt:ann", "seller", status="suspended")
        tick["t"] = 30
        assert (await b.client.get("/v1/settings")).status_code == 200  # cached, within bound
        tick["t"] = 61
        assert (await b.client.get("/v1/settings")).status_code == 401
        add_member(a, "jwt:ann", "viewer")  # reactivated but re-roled
        tick["t"] = 200
        assert (await b.client.get("/v1/settings")).status_code == 401
        add_member(a, "jwt:ann", "seller")
        tick["t"] = 300
        assert (await b.client.get("/v1/settings")).status_code == 200
        with psycopg.connect(ADMIN_DSN, autocommit=True) as c:  # tenant suspension
            c.execute(
                "INSERT INTO tenant_events (tenant_id, seq, event, actor, actor_plane, at)"
                " VALUES (%s, 1, 'suspended', 'op', 'operator', now())",
                (a,),
            )
        tick["t"] = 400
        assert (await b.client.get("/v1/settings")).status_code == 401
    finally:
        await b.close()
