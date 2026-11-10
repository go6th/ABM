"""SD-10: a tenant can only route sign-in for an email domain it proves it owns.

Before, ``verified_domains`` was whatever the tenant admin typed, so a tenant could list another
company's domain and send that company's staff to its own IdP from the sign-in page (and JIT
them into its own tenant). Now:

* saving a connection checks, for every listed domain, a DNS TXT record at ``_6th-sso.<domain>``
  whose value is bound to the saving tenant (``identity.domain_proof``); a missing record is a 422
  that names the exact record to publish, and nothing is saved;
* a domain another tenant has already proven is a 409;
* discovery and JIT trust only a connection whose domains were proven (``domains_proven_at``,
  migration 0117), and discovery answers "password" when two tenants hold the same proven domain.

GUARANTEE(test_a_tenant_cannot_claim_a_domain_without_its_dns_record)
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from identity import domain_proof
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


def _body(domain: str) -> dict[str, Any]:
    return {
        "slug": f"new-{uid()}",
        "protocol": "oidc",
        "issuer": "https://idp2.synthetic.test",
        "client_id": "c2",
        "authorization_endpoint": "https://idp2.synthetic.test/a",
        "token_endpoint": "https://idp2.synthetic.test/t",
        "jwks_uri": "https://idp2.synthetic.test/j",
        "verified_domains": [domain],
        "group_role_map": {"sales": "seller"},
    }


async def _admin(plane: Plane, browser: Browser, name: str) -> Any:
    tenant = new_tenant(name)
    slug = f"adm-{uid()}"
    add_oidc_connection(tenant, plane, slug)
    add_member(tenant, f"{slug}:root", "admin")
    await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
    return tenant


async def _discover(browser: Browser, email: str) -> dict[str, Any]:
    r = await browser.client.post(
        "/v1/sso/discover", json={"email": email}, headers={"Origin": BASE}
    )
    assert r.status_code == 200
    body: dict[str, Any] = r.json()
    return body


async def test_a_tenant_cannot_claim_a_domain_without_its_dns_record(
    plane: Plane, browser: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await _admin(plane, browser, "proof-a")
    domain = f"bigcorp-{uid()}.example"
    published: dict[str, list[str]] = {}

    async def lookup(name: str) -> list[str]:
        return published.get(name, [])

    monkeypatch.setattr(domain_proof, "txt_records", lookup)
    body = _body(domain)
    refused = await browser.call("POST", "/v1/sso/connections", json=body)
    assert refused.status_code == 422, refused.text
    record = domain_proof.expected(tenant, domain)
    assert record.name == f"_6th-sso.{domain}" and record.value in refused.text
    with psycopg.connect(ADMIN_DSN) as c:  # nothing was saved
        assert c.execute(
            "SELECT count(*) FROM idp_connections WHERE slug = %s", (body["slug"],)
        ).fetchone() == (0,)
    # a record bound to ANOTHER tenant does not prove it for this one
    published[record.name] = [domain_proof.expected(new_tenant("other"), domain).value]
    again = await browser.call("POST", "/v1/sso/connections", json=body)
    assert again.status_code == 422
    # the tenant's own record proves it: saved, and discovery now routes the domain to it
    published[record.name] = ["unrelated", record.value]
    created = await browser.call("POST", "/v1/sso/connections", json=body)
    assert created.status_code == 201, created.text
    found = await _discover(browser, f"ann@{domain}")
    assert found["next"] == "sso" and body["slug"] in str(found["start_url"])


async def test_a_domain_another_tenant_proved_is_refused(
    plane: Plane, browser: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    domain = f"owned-{uid()}.example"
    add_oidc_connection(new_tenant("proof-owner"), plane, f"own-{uid()}", verified_domains=[domain])
    tenant = await _admin(plane, browser, "proof-thief")

    async def lookup(name: str) -> list[str]:
        # even with a record for the thief published (a shared DNS host), the claim is refused
        return [domain_proof.expected(tenant, domain).value]

    monkeypatch.setattr(domain_proof, "txt_records", lookup)
    taken = await browser.call("POST", "/v1/sso/connections", json=_body(domain))
    assert taken.status_code == 409, taken.text


async def test_discovery_and_jit_ignore_unproven_or_ambiguous_domains(
    plane: Plane, browser: Browser
) -> None:
    unproven = f"unproven-{uid()}.example"
    slug = f"unp-{uid()}"
    tenant = new_tenant("proof-unproven")
    add_oidc_connection(
        tenant, plane, slug, verified_domains=[unproven], domains_proven_at=None, jit_enabled=True
    )
    assert (await _discover(browser, f"bo@{unproven}"))["next"] == "password"
    # JIT does not provision an address of an unproven domain either
    done = await browser.oidc_sign_in(
        slug, "newcomer", extra={"groups": ["analysts"], "email": f"new@{unproven}"}
    )
    assert done.status_code == 401
    with psycopg.connect(ADMIN_DSN) as c:
        assert c.execute(
            "SELECT count(*) FROM memberships WHERE tenant_id = %s", (tenant,)
        ).fetchone() == (0,)
    # two tenants holding one proven domain (rows from before the proof existed): ambiguous
    shared = f"shared-{uid()}.example"
    add_oidc_connection(new_tenant("proof-s1"), plane, f"s1-{uid()}", verified_domains=[shared])
    add_oidc_connection(new_tenant("proof-s2"), plane, f"s2-{uid()}", verified_domains=[shared])
    assert (await _discover(browser, f"bo@{shared}"))["next"] == "password"
