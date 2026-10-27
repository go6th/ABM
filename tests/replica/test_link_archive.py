"""The link archive end to end on PostgreSQL: a stored address is recorded with its captured
body, the newest copy of an address wins, the sheets attach the archived copy to each link, and
the account route serves the copy back (JSON or a readable page), never across tenants.
SYNTHETIC data only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence import archive as LA
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.hermetic import MemoryPayloadStore
from account_intelligence.replica import sheets as SH
from agents.ports import RunContext, SourceItem
from agents.production.payloads import StorePayloads
from tests.tracker.world import World, fetch, make_world

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
T0 = datetime(2026, 10, 27, 9, tzinfo=UTC)
PROFILE = "https://www.linkedin.com/in/synthetic-director"
PAGE = "https://synthetic-acme.example.com/news/leadership"


class _Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


def _ctx(w: World) -> RunContext:
    return RunContext(w.tenant, w.run, w.domain, "Synthetic Acme", w.domain, False, "sk-1")


def _archive(w: World, store: MemoryPayloadStore, at: datetime = T0) -> LA.LinkArchive:
    return LA.LinkArchive(tenant_txn, w.tenant, w.account, w.run, store, _Clock(at))


async def test_a_stored_link_is_archived_and_served_back() -> None:
    w = make_world()
    store = MemoryPayloadStore()
    archive = _archive(w, store)
    payloads = StorePayloads(store, w.account, archive=archive)
    ctx = _ctx(w)
    text = "Synthetic Director - Chief Information Officer - Synthetic Acme | LinkedIn\nA page."
    ref = await payloads.put(ctx, "key_contact_profiles_posts", SourceItem(PROFILE, text), "public")
    await payloads.put(ctx, "prior_customer_status", SourceItem(PAGE, "private notes"), "private")
    rows = fetch(
        "SELECT url, kind, payload_ref, format, title, owner_user_id FROM link_archive"
        " WHERE account_id = %s ORDER BY id",
        w.account,
    )
    assert rows == [
        (PROFILE, "profile", ref, "json", text.splitlines()[0], "test-owner")
    ]  # the private item is never archived; the owner is the account's
    async with tenant_txn(w.tenant) as s:
        found = await LA.lookup(s, w.account, [PROFILE, PAGE, "not a url"])
        profiles = await LA.profile_links(s, w.account)
    assert set(found) == {PROFILE} and found[PROFILE].payload_ref == ref
    assert [p.url for p in profiles] == [PROFILE]
    copy = await LA.archived_copy(store, found[PROFILE])
    assert (copy.url, copy.kind, copy.text, copy.archived_at) == (PROFILE, "profile", text, T0)
    # a copy the archive writes itself (a profile record, a detection): plain text in, JSON out
    key = await archive.put_text(PAGE, "page", "Omar Ali leads field service.", title="Leadership")
    async with tenant_txn(w.tenant) as s:
        page = (await LA.lookup(s, w.account, [PAGE]))[PAGE]
    assert page.payload_ref == key and key.startswith(
        f"tenants/{w.tenant}/accounts/{w.account}/archive/"
    )
    assert (await LA.archived_copy(store, page)).text == "Omar Ali leads field service."


async def test_the_newest_copy_of_an_address_wins() -> None:
    w = make_world()
    store = MemoryPayloadStore()
    first = await _archive(w, store, T0).put_text(PAGE, "page", "first capture")
    later = await _archive(w, store, T0 + timedelta(days=2)).put_text(
        PAGE, "page", "second capture"
    )
    # the same copy recorded twice is one row; a tracking parameter is the same address
    await _archive(w, store, T0 + timedelta(days=2)).put_text(PAGE, "page", "second capture")
    assert fetch("SELECT count(*) FROM link_archive WHERE account_id = %s", w.account)[0][0] == 2
    async with tenant_txn(w.tenant) as s:
        hit = (await LA.lookup(s, w.account, [PAGE + "?utm_source=mail"]))[
            PAGE + "?utm_source=mail"
        ]
    assert hit.payload_ref == later and hit.payload_ref != first
    assert (await LA.archived_copy(store, hit)).text == "second capture"
    # the sheets attach the copy to a row's links and source link
    rows: list[dict[str, Any]] = [
        {"n": 1, "cells": {}, "links": {"profile_url": PAGE}, "link": "https://nowhere.example/x"},
        {"n": 2, "cells": {}, "links": {}, "link": PAGE},
    ]
    async with tenant_txn(w.tenant) as s:
        await SH.attach_archives(s, w.account, rows, w.domain)
    assert list(rows[0]["archives"]) == ["profile_url"] and list(rows[1]["archives"]) == ["link"]
    href = rows[1]["archives"]["link"]["href"]
    assert href.startswith(f"/v1/accounts/{w.domain}/archive?url=https%3A%2F%2F")
    assert rows[0]["archives"]["profile_url"]["archived_at"].startswith("2026-10-29")


async def test_the_account_route_serves_the_archived_copy_to_its_tenant_only() -> None:
    from account_intelligence.api.auth import Identity, identity
    from tests.phase3.conftest import add_member, make_app
    from tests.phase3.test_ws4_views import _get

    w = make_world()
    store = MemoryPayloadStore()
    await _archive(w, store).put_text(
        PROFILE, "profile", "Synthetic Director\nCIO", title="Synthetic Director"
    )
    other = make_world()

    def plane_for(world: World) -> Any:
        add_member(world.tenant, "test-owner", "admin")
        plane = make_app()
        plane.app.state.container.payloads = store
        now = datetime.now(UTC)

        async def ident() -> Identity:
            return Identity("test-owner", world.tenant, "admin", now)

        plane.app.dependency_overrides[identity] = ident
        return plane

    plane = plane_for(w)
    path = f"/v1/accounts/{w.domain}/archive"
    r = await _get(plane, path, params={"url": PROFILE}, headers={"accept": "application/json"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["url"], body["kind"], body["title"], body["archived_at"]) == (
        PROFILE,
        "profile",
        "Synthetic Director",
        T0.isoformat(),
    )
    assert "Synthetic Director" in body["text"]
    page = await _get(plane, path, params={"url": PROFILE}, headers={"accept": "text/html"})
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "Archived copy captured on 27 October 2026" in page.text and PROFILE in page.text
    assert "<script" not in page.text
    missing = await _get(plane, path, params={"url": PAGE}, headers={"accept": "application/json"})
    assert missing.status_code == 404
    # another tenant's account of the same shape sees nothing of it
    stranger = await _get(
        plane_for(other), f"/v1/accounts/{w.domain}/archive", params={"url": PROFILE}
    )
    assert stranger.status_code == 404
    unknown = await _get(
        plane, f"/v1/accounts/{uuid4().hex}.example/archive", params={"url": PROFILE}
    )
    assert unknown.status_code == 404
