"""SAML ACS as the IdP posts it (fix prompt B, task 2): a cross-site form post whose Origin is the
IdP's, with no CSRF token. The web proxy lets exactly that request through
(web/lib/cross-site.ts); the API's own checks are what make it safe: the RelayState must name a
login state of this connection that is unexpired, unconsumed and bound to the pre-auth cookie of
the browser that started the sign-in. SYNTHETIC tenants only."""

from __future__ import annotations

import base64
import zlib
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import psycopg
import pytest

from tests.conftest import ADMIN_DSN
from tests.phase3.conftest import (
    BASE,
    Browser,
    Plane,
    add_member,
    add_saml_connection,
    new_tenant,
    uid,
)

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
IDP_ORIGIN = {"origin": "https://idp.example.com"}


def _refusals(tenant: UUID) -> list[str | None]:
    with psycopg.connect(ADMIN_DSN) as c:
        return [
            r[0]
            for r in c.execute(
                "SELECT reason_code FROM auth_events WHERE tenant_id = %s"
                " AND event = 'login_failed'"
                " ORDER BY id",
                (tenant,),
            ).fetchall()
        ]


async def _start(browser: Browser, plane: Plane, slug: str, subject: str) -> dict[str, Any]:
    """Start a SAML sign-in in ``browser`` and return the IdP's form fields."""
    start = await browser.client.get(f"/v1/sso/{slug}/start")
    assert start.status_code == 302, start.text
    q = {k: v[0] for k, v in parse_qs(urlsplit(start.headers["location"]).query).items()}
    xml = zlib.decompress(base64.b64decode(q["SAMLRequest"]), -15).decode()
    request_id = xml.split('ID="', 1)[1].split('"', 1)[0]
    response = plane.saml.response(
        request_id=request_id,
        acs_url=f"{BASE}/v1/sso/saml/acs",
        audience="https://app.test/saml/sp",
        subject=subject,
        now=plane.clock.now(),
        email=f"{subject}@synthetic.test",
    )
    return {"SAMLResponse": response, "RelayState": q["RelayState"]}


async def test_the_idps_cross_site_post_signs_in(plane: Plane, browser: Browser) -> None:
    a = new_tenant("saml-xsite")
    slug = f"acme-saml-{uid()}"
    add_saml_connection(a, plane, slug)
    add_member(a, f"{slug}:ann", "seller")
    form = await _start(browser, plane, slug, "ann")
    done = await browser.client.post("/v1/sso/saml/acs", data=form, headers=IDP_ORIGIN)
    assert done.status_code == 303, done.text
    assert "__Host-ai_session=" in done.headers.get("set-cookie", "")


async def test_a_relay_state_from_another_browser_is_refused(
    plane: Plane, browser: Browser
) -> None:
    a = new_tenant("saml-bind")
    slug = f"acme-saml-{uid()}"
    add_saml_connection(a, plane, slug)
    add_member(a, f"{slug}:ann", "seller")
    form = await _start(browser, plane, slug, "ann")
    thief = Browser(plane)
    try:
        await thief.client.get(f"/v1/sso/{slug}/start")  # its own, different pre-auth cookie
        stolen = await thief.client.post("/v1/sso/saml/acs", data=form, headers=IDP_ORIGIN)
        assert stolen.status_code == 401 and "set-cookie" not in stolen.headers
        bare = Browser(plane)  # no pre-auth cookie at all
        try:
            none = await bare.client.post("/v1/sso/saml/acs", data=form, headers=IDP_ORIGIN)
            assert none.status_code == 401
        finally:
            await bare.close()
    finally:
        await thief.close()
    assert _refusals(a).count("state_not_bound_to_browser") == 2
    # the browser that started it still finishes it once, and only once: a replay is refused even
    # with the original pre-auth cookie presented again
    preauth = browser.client.cookies.get("__Host-ai_preauth")
    assert (
        await browser.client.post("/v1/sso/saml/acs", data=form, headers=IDP_ORIGIN)
    ).status_code == 303
    browser.client.cookies.set("__Host-ai_preauth", preauth or "", domain="app.test")
    again = await browser.client.post("/v1/sso/saml/acs", data=form, headers=IDP_ORIGIN)
    assert again.status_code == 401
    assert "state_replayed" in _refusals(a)


async def test_an_unknown_relay_state_is_refused(plane: Plane, browser: Browser) -> None:
    a = new_tenant("saml-unknown")
    slug = f"acme-saml-{uid()}"
    add_saml_connection(a, plane, slug)
    form = await _start(browser, plane, slug, "ann")
    form["RelayState"] = "u" * 43
    done = await browser.client.post("/v1/sso/saml/acs", data=form, headers=IDP_ORIGIN)
    assert done.status_code == 401 and "set-cookie" not in done.headers
