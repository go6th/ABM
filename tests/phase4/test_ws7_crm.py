"""WS7 CRM write-back (CW1 to CW5). Lying scenarios: a client that grows an update method, an
ambiguous account match, a crash after the CRM create, a replayed request, Internal Only or
sensitive content, outreach wording, a provider name in a payload, a revoked connection, an
unapproved batch."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest

from account_intelligence.db.session import tenant_txn
from crm_writeback import client, hubspot, mapping, payloads, salesforce, service
from tests.phase4.conftest import NOW, db, tenant_with_account

PUBLIC = payloads.Item(
    "Hiring a platform owner in the SAP lane", "S-0004", "public", "expansion", True
)


@client.assert_create_only
@dataclass
class FakeCrmServer:
    """A CRM that only understands create and read; it records every request."""

    kind: str = "salesforce"
    requests: list[tuple[str, str]] = field(default_factory=list)
    records: dict[str, client.Draft] = field(default_factory=dict)
    crash_after_create: bool = False

    async def create(self, draft: client.Draft) -> client.Created:
        """Create one new record."""
        self.requests.append(("create", draft.marker))
        rid = f"rec{len(self.records) + 1}"
        self.records[rid] = draft
        if self.crash_after_create:
            raise ConnectionResetError("lost after the create")
        return client.Created(rid)

    async def find_by_marker(self, marker: str) -> client.Created | None:
        """Read only."""
        self.requests.append(("find_by_marker", marker))
        hit = [r for r, d in self.records.items() if d.marker == marker]
        return client.Created(hit[0]) if hit else None

    async def find_account(self, domain: str) -> list[str]:
        """Read only."""
        self.requests.append(("find_account", domain))
        return ["001A"]

    def __getattr__(self, name: str) -> Any:  # any other request is rejected loudly
        raise AssertionError(f"non-create request {name}")


def test_the_crm_client_cannot_overwrite_or_delete() -> None:
    for cls in (salesforce.SalesforceCrm, hubspot.HubSpotCrm, FakeCrmServer):
        public = {n for n, _ in inspect.getmembers(cls, callable) if not n.startswith("_")}
        assert public <= {"create", "find_by_marker", "find_account"}, cls

    class Sneaky:  # the mutation: re-add an update path
        kind = "salesforce"

        async def create(self, d: client.Draft) -> client.Created: ...
        async def update_account(self, rid: str, fields: dict[str, str]) -> None: ...

    with pytest.raises(client.NotCreateOnly):
        client.assert_create_only(Sneaky)

    class Renamed:
        async def create(self, d: client.Draft) -> client.Created: ...
        async def upsert(self, d: client.Draft) -> None: ...

    with pytest.raises(client.NotCreateOnly):
        client.assert_create_only(Renamed)


@pytest.mark.asyncio
async def test_production_adapters_refuse_until_configured() -> None:
    from account_intelligence.platform.ports import NotConfigured

    for c in (salesforce.SalesforceCrm(), hubspot.HubSpotCrm()):
        with pytest.raises(NotConfigured):
            await c.find_account("x.example")


def test_ambiguous_or_unconfirmed_mappings_write_nothing() -> None:
    assert mapping.resolve([], None) == ("missing", None)
    assert mapping.resolve(["a", "b"], None) == ("ambiguous", None)
    assert mapping.resolve(["a"], None) == ("unconfirmed", None)  # one match still needs a human
    assert mapping.resolve(["a"], "z") == ("unconfirmed", None)
    assert mapping.resolve(["a", "b"], "b") == ("mapped", "b")
    for cands, conf in (([], None), (["a", "b"], None), (["a"], None)):
        with pytest.raises(service.WriteRefused):
            service.preview(
                "note", candidates=cands, confirmed=conf, account_label="acme", items=[PUBLIC]
            )


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        (payloads.Item("x", "S-0001", "private", "expansion", True), "not_public"),
        (payloads.Item("x", "S-0001", "public", "expansion", False), "not_delivered"),
        (payloads.Item("x", "S-0001", "public", "data_breach", True), "sensitive_finding"),
        (
            payloads.Item(
                "Great time to reach out to the CIO", "S-0001", "public", "expansion", True
            ),
            "outreach_wording",
        ),
        (payloads.Item("y" * 5000, "S-0001", "public", "expansion", True), "too_long"),
    ],
)
def test_crm_payloads_are_whitelisted_and_scanned(item: payloads.Item, reason: str) -> None:
    with pytest.raises(payloads.PayloadRefused) as e:
        payloads.render(
            "note",
            account_record_id="001A",
            account_label="acme",
            items=[item],
            marker_prefix="m:",
            title_chars=120,
            body_chars=4000,
        )
    assert e.value.reason == reason
    with pytest.raises(payloads.PayloadRefused):
        payloads.render(
            "note",
            account_record_id="001A",
            account_label="acme",
            items=[],
            marker_prefix="m:",
            title_chars=120,
            body_chars=4000,
        )
    task = payloads.render(
        "task",
        account_record_id="001A",
        account_label="acme",
        items=[PUBLIC],
        marker_prefix="m:",
        title_chars=120,
        body_chars=4000,
    )
    assert task.body.startswith("Preparation:") and task.marker in task.body


def test_a_provider_name_blocks_the_payload() -> None:
    from account_intelligence.connectors.registry_internal import vendor_names
    from account_intelligence.connectors.settings import load_providers_config

    vendor = sorted(n for n in vendor_names(load_providers_config()).values() if n != "Parallel")[0]
    with pytest.raises(payloads.PayloadRefused) as e:
        payloads.render(
            "note",
            account_record_id="001A",
            account_label="acme",
            items=[payloads.Item(f"Found via {vendor}", "S-0001", "public", "expansion", True)],
            marker_prefix="m:",
            title_chars=120,
            body_chars=4000,
        )
    assert e.value.reason == "client_output_scan"


async def _setup(name: str) -> tuple[UUID, UUID, client.Draft]:
    t, a = tenant_with_account(name)
    async with tenant_txn(t) as s:
        await service.enable(s, "salesforce", actor="admin", now=NOW)
        await service.confirm_mapping(
            s, account_id=a, crm="salesforce", record_id="001A", actor="admin", now=NOW
        )
        confirmed = await service.confirmed_record(s, a, "salesforce")
    draft = service.preview(
        "note",
        candidates=["001A"],
        confirmed=confirmed,
        account_label="acme.example",
        items=[PUBLIC],
    )
    return t, a, draft


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_write_back_flow_is_opt_in_mapped_approved_and_idempotent() -> None:
    t, a, draft = await _setup("p4-crm-flow")
    crm = FakeCrmServer()
    async with tenant_txn(t) as s:
        r1 = await service.request(
            s, account_id=a, crm="salesforce", draft=draft, actor="u", now=NOW
        )
        r2 = await service.request(
            s, account_id=a, crm="salesforce", draft=draft, actor="u", now=NOW
        )  # a replay
    assert r1.new and not r2.new and r1.write_id == r2.write_id
    # not approved yet: refused, nothing sent
    assert await service.execute(tenant_txn, t, crm, r1.write_id, actor="u", now=NOW) == "refused"
    assert crm.requests == []
    t2, a2, d2 = await _setup("p4-crm-flow-2")
    async with tenant_txn(t2) as s:
        w = (
            await service.request(s, account_id=a2, crm="salesforce", draft=d2, actor="u", now=NOW)
        ).write_id
        assert await service.approve(s, [w], actor="admin", now=NOW) == 1
        assert await service.approve(s, [w], actor="admin", now=NOW) == 0
    assert await service.execute(tenant_txn, t2, crm, w, actor="u", now=NOW) == "created"
    assert await service.execute(tenant_txn, t2, crm, w, actor="u", now=NOW) == "created"
    assert [r[0] for r in crm.requests] == ["create"]  # one create, ever
    with pytest.raises(service.WriteRefused):
        async with tenant_txn(t2) as s:
            await service.approve(s, [w] * 26, actor="admin", now=NOW)
    events = [
        e for (e,) in db("SELECT event FROM crm_write_events WHERE write_id = %s ORDER BY id", w)
    ]
    assert events == ["requested", "approved", "sending", "created"]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_crash_after_create_is_adopted_never_repeated_and_revoke_stops_writes() -> None:
    t, a, draft = await _setup("p4-crm-crash")
    crm = FakeCrmServer(crash_after_create=True)
    async with tenant_txn(t) as s:
        w = (
            await service.request(
                s, account_id=a, crm="salesforce", draft=draft, actor="u", now=NOW
            )
        ).write_id
        await service.approve(s, [w], actor="admin", now=NOW)
    # the create succeeded at the CRM but the answer was lost: the outcome is unknown, not failed
    assert await service.execute(tenant_txn, t, crm, w, actor="u", now=NOW) == "unknown"
    assert await service.execute(tenant_txn, t, crm, w, actor="u", now=NOW) == "sending"
    assert await service.reconcile(tenant_txn, t, crm, actor="system", now=NOW) == {
        str(w): "adopted"
    }
    assert [r[0] for r in crm.requests] == ["create", "find_by_marker"]  # never a second create
    # ...a write with a 'sending' event and no outcome is reconciled by marker, never re-sent
    t3, a3, d3 = await _setup("p4-crm-crash-2")
    crm3 = FakeCrmServer()
    async with tenant_txn(t3) as s:
        w3 = (
            await service.request(s, account_id=a3, crm="salesforce", draft=d3, actor="u", now=NOW)
        ).write_id
        await service.approve(s, [w3], actor="admin", now=NOW)
        await s.execute(
            __import__("sqlalchemy").text(
                "INSERT INTO crm_write_events (write_id, event, actor, at) VALUES (:w, 'sending',"
                " 'u', now())"
            ),
            {"w": w3},
        )
    await crm3.create(d3)  # the CRM has the record; our process died before recording it
    crm3.requests.clear()
    assert await service.execute(tenant_txn, t3, crm3, w3, actor="u", now=NOW) == "sending"
    assert await service.reconcile(tenant_txn, t3, crm3, actor="system", now=NOW) == {
        str(w3): "adopted"
    }
    assert [r[0] for r in crm3.requests] == ["find_by_marker"]  # read only, no second create
    # revoking stops the next write at once
    async with tenant_txn(t3) as s:
        await service.revoke(s, "salesforce", actor="admin", now=NOW)
        d4 = service.preview(
            "task", candidates=["001A"], confirmed="001A", account_label="acme", items=[PUBLIC]
        )
        w4 = (
            await service.request(s, account_id=a3, crm="salesforce", draft=d4, actor="u", now=NOW)
        ).write_id
        await service.approve(s, [w4], actor="admin", now=NOW)
    assert await service.execute(tenant_txn, t3, crm3, w4, actor="u", now=NOW) == "refused"
    assert not any(r[0] == "create" for r in crm3.requests)
