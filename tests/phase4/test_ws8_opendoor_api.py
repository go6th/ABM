"""WS8 Open Door and the Phase 4 API (OD1, OD2, AL3, AL8, CG2, CW3). Lying scenarios: an event
that drifts from its schema, a breaking schema change inside v1, a cursor from another tenant's
data, consumer or sending code behind the contract, a subscription naming another tenant's
account, a budget view with units, a CRM write before opt-in."""

from __future__ import annotations

import copy
from pathlib import Path
from uuid import UUID

import jsonschema
import pytest

from account_intelligence.db.session import tenant_txn
from opendoor import api as od
from opendoor import events
from tests.phase3.conftest import (  # noqa: F401  (fixtures)
    Browser,
    Plane,
    add_member,
    add_oidc_connection,
    uid,
)
from tests.phase4.conftest import db, fact, tenant_with_account
from tests.phase4.test_ws5_alerting import evaluate, make_event

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = events.schema("signal_event.v1.json")["examples"][0]


def test_open_door_events_validate_and_breaking_changes_are_refused() -> None:
    events.validate("signal_event.v1.json", EXAMPLE)
    for broken in (
        {**EXAMPLE, "grade": "A"},
        {**EXAMPLE, "provider": "x"},
        {k: v for k, v in EXAMPLE.items() if k != "fact_ref"},
        {**EXAMPLE, "source_codes": ["raw-payload"]},
        {**EXAMPLE, "score": -1},
    ):
        with pytest.raises(jsonschema.ValidationError):
            events.validate("signal_event.v1.json", broken)
    old = events.schema("signal_event.v1.json")
    additive = copy.deepcopy(old)
    additive["properties"]["region"] = {"type": "string"}
    additive["properties"]["grade"]["enum"].append("internal")
    assert events.breaking_changes(old, additive) == []
    for mutate, why in (
        (lambda s: s["properties"].pop("lead"), "removed"),
        (lambda s: s["required"].append("lead"), "became required"),
        (lambda s: s["properties"]["score"].update(type="string"), "type changed"),
        (lambda s: s["properties"]["grade"].update(enum=["confirmed"]), "enum changed"),
        (lambda s: s["properties"]["account"].update(pattern="^x$"), "pattern changed"),
    ):
        new = copy.deepcopy(old)
        mutate(new)
        assert any(why in p for p in events.breaking_changes(old, new)), why
    page = events.schema("signal_page.v1.json")
    assert events.breaking_changes(page, page) == []


def test_no_consumer_scheduler_or_sending_code_behind_the_contract() -> None:
    import ast

    names: set[str] = set()
    imports: set[str] = set()
    for p in (ROOT / "src/opendoor").rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Name | ast.arg):
                names.add(node.id if isinstance(node, ast.Name) else node.arg)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.add(node.name)
            elif isinstance(node, ast.Import | ast.ImportFrom):
                imports |= {a.name for a in node.names} | {getattr(node, "module", "") or ""}
    words = ("consum", "sequence", "schedul", "send", "mailer", "slack", "dispatch")
    assert not {n for n in names if any(w in n.lower() for w in words)}
    assert not imports & {"httpx", "requests", "aiohttp", "smtplib", "alerts", "crm_writeback"}


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_signal_pages_validate_and_paginate_inside_the_tenant() -> None:
    t, a = tenant_with_account("p4-od", "od.example")
    for i in range(5):
        await make_event(t, a, signal="news_event", title=f"story number {i} distinct words {i}")
    other_t, other_a = tenant_with_account("p4-od-other")
    await make_event(other_t, other_a, signal="news_event", title="the other tenant")
    seen: list[str] = []
    cursor = None
    async with tenant_txn(t) as s:
        for _ in range(5):  # LOOP(test paging): bounded
            body = await od.page(s, cursor, limit=2)
            seen += [i["event_id"] for i in body["items"]]
            assert all(i["account"] == "od.example" for i in body["items"])
            cursor = body["next_cursor"]
            if cursor is None:
                break
    assert len(seen) == len(set(seen)) == 5
    with pytest.raises(ValueError):
        od.decode_cursor("not-a-cursor")
    del other_a


async def _signed_in(
    plane: Plane, browser: Browser, name: str, role: str = "admin"
) -> tuple[UUID, UUID, str]:
    t, a = tenant_with_account(name, f"{name}.example")
    slug = f"acme-{uid()}"
    add_oidc_connection(t, plane, slug)
    add_member(t, f"{slug}:root", role)
    groups = {"admin": "admins", "seller": "sales", "viewer": "readers", "analyst": "analysts"}
    await browser.oidc_sign_in(slug, "root", extra={"groups": [groups[role]]})
    return t, a, f"{slug}:root"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_phase4_api_is_tenant_scoped_role_gated_and_client_safe(
    plane: Plane,
    browser: Browser,
) -> None:
    from account_intelligence.safety.client_output import scan_text

    t, a, user = await _signed_in(plane, browser, "p4api")
    defaults = (await browser.client.get("/v1/subscriptions")).json()
    assert defaults == {
        "digest": True,
        "immediate_rules": [],
        "slack": False,
        "threshold": 0,
        "muted_accounts": [],
    }  # subscribe up
    other_t, other_a = tenant_with_account("p4api-other")
    bad = await browser.call("PUT", "/v1/subscriptions", json={"muted_accounts": [str(other_a)]})
    assert bad.status_code == 404  # another tenant's account cannot be named
    assert (
        await browser.call("PUT", "/v1/subscriptions", json={"immediate_rules": ["made_up"]})
    ).status_code == 422
    up = await browser.call("POST", "/v1/subscriptions/subscribe-all")
    assert up.status_code == 200 and "new_build_role" in up.json()["immediate_rules"]
    assert db(
        "SELECT bulk FROM subscriptions WHERE tenant_id = %s ORDER BY version DESC LIMIT 1", t
    ) == [(True,)]
    assert db(
        "SELECT count(*) FROM audit_events WHERE tenant_id = %s AND action LIKE"
        " '%%subscriptions_bulk'",
        t,
    ) == [(1,)]
    ev = await make_event(t, a, signal="build_role_posting")
    out = await evaluate(t, ev, users=(user,))
    inbox = (await browser.client.get("/v1/alerts")).json()["alerts"]
    assert [x["rule"] for x in inbox] == ["new_build_role"]
    rate = await browser.call(
        "POST",
        f"/v1/alerts/{out.alerts[0]}/feedback",
        json={"verdict": "useful", "reason": "acted_on"},
    )
    assert rate.status_code == 201
    assert (
        await browser.call(
            "POST",
            f"/v1/alerts/{out.alerts[0]}/feedback",
            json={"verdict": "useful", "reason": "wrong"},
        )
    ).status_code == 422
    other_ev = await make_event(other_t, other_a, signal="build_role_posting")
    other_out = await evaluate(other_t, other_ev, users=("someone",))
    assert (
        await browser.call(
            "POST",
            f"/v1/alerts/{other_out.alerts[0]}/feedback",
            json={"verdict": "useful", "reason": "acted_on"},
        )
    ).status_code == 404
    useful = (await browser.client.get("/v1/alerts/usefulness")).json()
    assert useful["target_is_a_result"] is False and useful["small_base"] is True
    sig = (await browser.client.get("/v1/signals")).json()
    assert sig["schema"] == "signal_page.v1" and len(sig["items"]) == 1
    assert (await browser.client.get("/v1/signals", params={"cursor": "%%%"})).status_code == 422
    budget = (await browser.client.get("/v1/monitoring/budget")).json()
    assert set(budget) == {"period", "percent"}
    hist = await browser.client.get(f"/v1/accounts/{'p4api.example'}/history")
    assert hist.status_code == 200 and hist.json() == {"history": []}
    assert (await browser.client.get("/v1/accounts/nope.example/history")).status_code == 404
    for path in ("/v1/alerts", "/v1/signals", "/v1/monitoring/budget", "/v1/subscriptions"):
        text_ = (await browser.client.get(path)).text
        assert not scan_text(text_), path


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_crm_routes_are_opt_in_admin_only_and_write_nothing_unmapped(
    plane: Plane,
    browser: Browser,
) -> None:
    t, a, _ = await _signed_in(plane, browser, "p4crm")
    domain = "p4crm.example"
    early = await browser.call("POST", "/v1/crm/salesforce/writes", json={"domain": domain})
    assert early.status_code == 409  # not enabled: nothing requested
    assert (await browser.call("POST", "/v1/crm/salesforce/enable")).status_code == 200
    unmapped = await browser.call("POST", "/v1/crm/salesforce/preview", json={"domain": domain})
    assert unmapped.status_code == 422 and "mapping_" in unmapped.text
    f = fact(t, a, grade="confirmed")
    assert f
    mapped = await browser.call(
        "POST", "/v1/crm/salesforce/mappings", json={"domain": domain, "record_id": "001A"}
    )
    assert mapped.status_code == 201
    prev = await browser.call("POST", "/v1/crm/salesforce/preview", json={"domain": domain})
    assert prev.status_code == 200, prev.text
    w1 = (await browser.call("POST", "/v1/crm/salesforce/writes", json={"domain": domain})).json()
    w2 = (await browser.call("POST", "/v1/crm/salesforce/writes", json={"domain": domain})).json()
    assert w1["write_id"] == w2["write_id"] and w1["new"] and not w2["new"]
    ok = await browser.call(
        "POST", "/v1/crm/salesforce/writes/approve", json={"write_ids": [w1["write_id"]]}
    )
    assert ok.status_code == 200 and ok.json() == {"approved": 1}
    listed = (await browser.client.get("/v1/crm/salesforce/writes")).json()["writes"]
    assert listed[0]["events"] == ["requested", "approved"]
    assert (
        db("SELECT count(*) FROM audit_events WHERE tenant_id = %s AND action LIKE '%%crm_%%'", t)[
            0
        ][0]
        >= 4
    )
    seller = Browser(plane)
    try:
        await _signed_in(plane, seller, "p4crm-seller", role="seller")
        refused = await seller.call("POST", "/v1/crm/salesforce/enable")
        assert refused.status_code == 403
    finally:
        await seller.close()
