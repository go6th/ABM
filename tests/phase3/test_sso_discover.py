"""Sign-in discovery (``POST /v1/sso/discover``) and "Keep me signed in on this device" through the
real routes on PostgreSQL. SYNTHETIC tenants only.

Discovery answers "continue with SSO" (with the start URL) or "use your password", in the same
shape and status whether the domain is unknown, belongs to a disabled connection or to a company
with no SSO at all, so it never says whether a company or an account exists.
"""

from __future__ import annotations

import psycopg
import pytest

from tests.conftest import ADMIN_DSN
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

ORIGIN = {"Origin": BASE}


async def _discover(browser: Browser, email: str) -> tuple[int, dict[str, object]]:
    r = await browser.client.post("/v1/sso/discover", json={"email": email}, headers=ORIGIN)
    return r.status_code, r.json()


async def test_a_company_domain_with_sso_continues_to_its_start_url(
    plane: Plane, browser: Browser
) -> None:
    tenant = new_tenant("discover-found")
    slug, domain = f"disc-{uid()}", f"found-{uid()}.example"
    add_oidc_connection(tenant, plane, slug, verified_domains=[domain])

    status, body = await _discover(browser, f"Ann.Lee@{domain.upper()}")
    assert status == 200
    assert body == {"next": "sso", "start_url": f"/v1/sso/{slug}/start?return_to=%2F"}
    # The URL it hands back really starts that company's sign-in.
    start = await browser.client.get(str(body["start_url"]))
    assert start.status_code == 302


async def test_unknown_disabled_and_password_only_domains_all_answer_the_same(
    plane: Plane, browser: Browser
) -> None:
    disabled_tenant = new_tenant("discover-disabled")
    disabled_slug, disabled_domain = f"off-{uid()}", f"off-{uid()}.example"
    add_oidc_connection(disabled_tenant, plane, disabled_slug, verified_domains=[disabled_domain])
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            "UPDATE idp_connections SET status = 'disabled' WHERE slug = %s", (disabled_slug,)
        )
    password_only, password_domain = new_tenant("discover-password"), f"pw-{uid()}.example"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            "INSERT INTO users (tenant_id, subject, email) VALUES (%s, %s, %s)",
            (password_only, f"pw-{uid()}", f"bo@{password_domain}"),
        )

    replies = [
        await _discover(browser, f"someone@nobody-{uid()}.example"),
        await _discover(browser, f"someone@{disabled_domain}"),
        await _discover(browser, f"bo@{password_domain}"),
    ]
    assert replies == [(200, {"next": "password", "start_url": None})] * 3
    # Nothing about any tenant appears: no id, no name, no slug.
    text = repr(replies)
    for value in (str(disabled_tenant), str(password_only), disabled_slug, "synthetic"):
        assert value not in text


async def test_discovery_needs_the_app_origin_and_a_real_email(browser: Browser) -> None:
    cross = await browser.client.post(
        "/v1/sso/discover",
        json={"email": "a@b.example"},
        headers={"Origin": "https://evil.example"},
    )
    assert cross.status_code == 403
    bad = await browser.client.post("/v1/sso/discover", json={"email": "nope"}, headers=ORIGIN)
    assert bad.status_code == 422


async def test_discovery_is_rate_limited_per_domain(browser: Browser) -> None:
    domain = f"busy-{uid()}.example"
    codes = [
        (
            await browser.client.post(
                "/v1/sso/discover", json={"email": f"u{i}@{domain}"}, headers=ORIGIN
            )
        ).status_code
        for i in range(12)
    ]
    assert codes[:10] == [200] * 10
    assert codes[-1] == 429


async def _password_user(plane: Plane, password: str) -> str:
    from identity.local_password import hash_password

    tenant = new_tenant("remember")
    subject, email = f"rem-{uid()}", f"rem-{uid()}@synthetic.test"
    digest = str(await hash_password(password, email=email))
    now = plane.clock.now()
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            "INSERT INTO users (tenant_id, subject, email) VALUES (%s, %s, %s)",
            (tenant, subject, email),
        )
        c.execute(
            "INSERT INTO memberships (tenant_id, user_id, role, status) VALUES (%s, %s,"
            " 'analyst', 'active')",
            (tenant, subject),
        )
        c.execute(
            "INSERT INTO local_identities (email, password_hash, status, email_verified_at,"
            " password_changed_at, created_at, updated_at) VALUES (%s, %s, 'active', %s, %s, %s,"
            " %s)",
            (email, digest, now, now, now, now),
        )
    return email


def _session_cookie(headers: list[str], name: str) -> str:
    return next(h for h in headers if h.startswith(f"{name}="))


async def test_keep_me_signed_in_sets_the_cookie_lifetime(plane: Plane, browser: Browser) -> None:
    from identity.sessions import load_policy

    policy = load_policy()
    password = "Long-unique-amber-credential-7731"
    email = await _password_user(plane, password)

    kept = await browser.client.post(
        "/v1/auth/login", json={"email": email, "password": password}, headers=ORIGIN
    )
    assert kept.status_code == 200, kept.text
    cookie = _session_cookie(kept.headers.get_list("set-cookie"), policy.session_cookie)
    assert f"Max-Age={policy.absolute_timeout_s}" in cookie

    once = await browser.client.post(
        "/v1/auth/login",
        json={"email": email, "password": password, "remember": False},
        headers=ORIGIN,
    )
    assert once.status_code == 200, once.text
    cookie = _session_cookie(once.headers.get_list("set-cookie"), policy.session_cookie)
    # A browser-session cookie: no Max-Age and no Expires, so it ends when the browser closes.
    assert "max-age" not in cookie.lower() and "expires" not in cookie.lower()
    assert "HttpOnly" in cookie and "Secure" in cookie
    # The session it carries is a normal session.
    assert (await browser.client.get("/v1/sso/session")).status_code == 200


async def test_keep_me_signed_in_off_also_holds_for_an_sso_sign_in(
    plane: Plane, browser: Browser
) -> None:
    from identity.sessions import load_policy

    policy = load_policy()
    tenant = new_tenant("remember-sso")
    slug = f"once-{uid()}"
    add_oidc_connection(tenant, plane, slug)
    add_member(tenant, f"{slug}:ann", "seller")

    start = await browser.client.get(
        f"/v1/sso/{slug}/start", params={"return_to": "/", "remember": "false"}
    )
    assert start.status_code == 302
    state, code = plane.oidc.authorize(
        start.headers["location"], subject="ann", now=plane.clock.now()
    )
    done = await browser.client.get("/v1/sso/oidc/callback", params={"state": state, "code": code})
    assert done.status_code == 303, done.text
    cookie = _session_cookie(done.headers.get_list("set-cookie"), policy.session_cookie)
    assert "max-age" not in cookie.lower() and "expires" not in cookie.lower()

    again = await browser.oidc_sign_in(slug, "ann")
    assert again.status_code == 303
    cookie = _session_cookie(again.headers.get_list("set-cookie"), policy.session_cookie)
    assert f"Max-Age={policy.absolute_timeout_s}" in cookie
