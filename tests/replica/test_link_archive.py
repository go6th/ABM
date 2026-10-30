"""The link archive end to end on PostgreSQL: a stored address is recorded with its captured
body, the newest copy of an address wins, the sheets attach the archived copy to each link, and
the account route serves the copy back as JSON, never across tenants.
SYNTHETIC data only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence import archive as LA
from account_intelligence import archive_records as AR
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
    await payloads.put(ctx, "key_contact_profiles_posts", SourceItem(PROFILE, text), "public")
    await payloads.put(ctx, "prior_customer_status", SourceItem(PAGE, "private notes"), "private")
    rows = fetch(
        "SELECT url, kind, payload_ref, format, title, owner_user_id FROM link_archive"
        " WHERE account_id = %s ORDER BY id",
        w.account,
    )
    # the private item is never archived; the owner is the account's; the copy is the structured
    # record (a neutral archive key), not the raw item
    assert [(r[0], r[1], r[3], r[4], r[5]) for r in rows] == [
        (PROFILE, "profile", "json", text.splitlines()[0], "test-owner")
    ]
    ref = str(rows[0][2])
    assert ref.startswith(f"tenants/{w.tenant}/accounts/{w.account}/archive/")
    async with tenant_txn(w.tenant) as s:
        found = await LA.lookup(s, w.account, [PROFILE, PAGE, "not a url"])
        profiles = await LA.profile_links(s, w.account)
    assert set(found) == {PROFILE} and found[PROFILE].payload_ref == ref
    assert [p.url for p in profiles] == [PROFILE]
    copy = await LA.archived_copy(store, found[PROFILE])
    assert (copy.url, copy.kind, copy.text, copy.archived_at) == (PROFILE, "profile", text, T0)
    assert copy.record is not None and copy.record["schema"] == "page-archive/1"
    assert copy.record["publisher"] == "linkedin.com" and copy.record["source"] == "6th agents"
    # the same item stored again (an unchanged page of a later research) adds no second copy
    await payloads.put(ctx, "key_contact_profiles_posts", SourceItem(PROFILE, text), "public")
    assert fetch("SELECT count(*) FROM link_archive WHERE account_id = %s", w.account)[0][0] == 1
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
    # the archived copy is a page of the website, not the API path
    assert href.startswith(f"/accounts/{w.domain}/archive?url=https%3A%2F%2F")
    assert rows[0]["archives"]["profile_url"]["archived_at"].startswith("2026-10-29")


async def test_the_account_route_serves_the_archived_copy_to_its_tenant_only() -> None:
    from account_intelligence.api.auth import Identity, identity
    from tests.phase3.conftest import add_member, make_app
    from tests.phase3.test_ws4_views import _get

    w = make_world()
    store = MemoryPayloadStore()
    await _archive(w, store).put_text(
        PAGE, "page", "Synthetic Director\nCIO", title="Synthetic Director"
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
    r = await _get(plane, path, params={"url": PAGE}, headers={"accept": "application/json"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["url"], body["kind"], body["title"], body["archived_at"]) == (
        PAGE,
        "page",
        "Synthetic Director",
        T0.isoformat(),
    )
    assert "Synthetic Director" in body["text"]
    # the readable page is the website's: the route answers JSON even to a browser's Accept
    page = await _get(plane, path, params={"url": PAGE}, headers={"accept": "text/html"})
    assert page.status_code == 200 and page.headers["content-type"].startswith("application/json")
    assert "<style" not in page.text and page.json()["url"] == PAGE
    missing = await _get(
        plane, path, params={"url": PAGE + "/other"}, headers={"accept": "application/json"}
    )
    assert missing.status_code == 404
    # another tenant's account of the same shape sees nothing of it
    stranger = await _get(
        plane_for(other), f"/v1/accounts/{w.domain}/archive", params={"url": PAGE}
    )
    assert stranger.status_code == 404
    unknown = await _get(plane, f"/v1/accounts/{uuid4().hex}.example/archive", params={"url": PAGE})
    assert unknown.status_code == 404


def _plane_for(world: World, store: MemoryPayloadStore) -> Any:
    from account_intelligence.api.auth import Identity, identity
    from tests.phase3.conftest import add_member, make_app

    add_member(world.tenant, "test-owner", "admin")
    plane = make_app()
    plane.app.state.container.payloads = store
    now = datetime.now(UTC)

    async def ident() -> Identity:
        return Identity("test-owner", world.tenant, "admin", now)

    plane.app.dependency_overrides[identity] = ident
    return plane


async def _posting_copy(
    w: World, store: MemoryPayloadStore, url: str, at: datetime, **fields: Any
) -> None:
    """Archive a structured posting record the way the job repository does (synthetic)."""
    from jobs import snapshot as SN

    record: dict[str, Any] = {
        "schema": SN.SCHEMA,
        "url": url,
        "title": "Platform Engineer",
        "requisition": "R-1001",
        "location": "Springfield, US",
        "status_at_capture": "closed",
        "first_seen": "2026-09-01",
        "last_seen": "2026-10-01",
        "posted_at": None,
        "seniority": "manager",
        "platforms": ["ServiceNow"],
        "class": "Build role",
        "platform_sentences": [{"platform": "ServiceNow", "text": "Build ServiceNow flows."}],
        "description": "Line one\nLine two",
        "captured_at": at.isoformat(),
        "source": SN.SOURCE,
        **fields,
    }
    key = LA.archive_key(w.tenant, w.account, SN.identity_bytes(record))
    await store.put(key, SN.snapshot_bytes(record))
    async with tenant_txn(w.tenant) as s:
        await LA.record(
            s,
            w.tenant,
            w.account,
            w.run,
            [
                LA.ArchiveEntry(
                    url,
                    "posting",
                    key,
                    at,
                    "json",
                    title=record["title"],
                    meta=AR.index_meta(record),
                )
            ],
        )


async def test_the_archive_route_serves_the_structured_posting_and_a_download() -> None:
    from tests.phase3.test_ws4_views import _get

    w = make_world()
    store = MemoryPayloadStore()
    url = "https://jobs.synthetic-acme.example/p1"
    await _posting_copy(w, store, url, T0)
    plane = _plane_for(w, store)
    path = f"/v1/accounts/{w.domain}/archive"
    r = await _get(plane, path, params={"url": url})
    body = r.json()
    assert r.status_code == 200 and body["kind"] == "posting"
    assert body["text"] == "Line one\nLine two"  # the description is the copy's text
    assert body["record"]["schema"] == "posting-archive/1"
    assert (
        body["record"]["status_at_capture"] == "closed"
        and body["record"]["requisition"] == "R-1001"
    )
    file = await _get(plane, path, params={"url": url, "download": "true"})
    assert file.status_code == 200
    assert "attachment" in file.headers["content-disposition"]
    assert file.json() == body["record"]  # the record itself, not the envelope


async def test_the_archive_route_blanks_a_refused_field_not_the_copy() -> None:
    from tests.phase3.test_ws4_views import _get

    w = make_world()
    store = MemoryPayloadStore()
    url = "https://jobs.synthetic-acme.example/p2"
    leak = "Reach it through the broker at https://a.example"
    await _posting_copy(w, store, url, T0, description=leak)
    plane = _plane_for(w, store)
    from unittest import mock

    def scrub(text: str) -> tuple[str, bool]:
        return text, text == leak

    with mock.patch("agents.naming.scrub_client_text", scrub):
        r = await _get(plane, f"/v1/accounts/{w.domain}/archive", params={"url": url})
    body = r.json()
    assert r.status_code == 200 and body["text"] == "" and body["record"]["description"] == ""
    assert "record.description" in body["record"]["withheld"]
    assert body["record"]["title"] == "Platform Engineer"


async def test_the_archive_list_pages_newest_first_and_stays_in_its_tenant() -> None:
    from tests.phase3.test_ws4_views import _get

    w = make_world()
    store = MemoryPayloadStore()
    urls = [f"https://jobs.synthetic-acme.example/j{i}" for i in range(5)]
    for i, url in enumerate(urls):
        await _posting_copy(w, store, url, T0 + timedelta(hours=i), location=f"Town {i}")
    # a newer copy of one address supersedes its older one in the index
    await _posting_copy(w, store, urls[0], T0 + timedelta(days=3), status_at_capture="open")
    await _archive(w, store).put_text(PAGE, "page", "A page.", title="News")
    other = make_world()
    await _posting_copy(other, store, "https://jobs.other-tenant.example/x", T0)
    plane = _plane_for(w, store)
    path = f"/v1/accounts/{w.domain}/archive/list"
    first = await _get(plane, path, params={"kind": "posting", "limit": "2"})
    page = first.json()
    assert first.status_code == 200 and page["total"] == 5 and page["next_offset"] == 2
    assert [i["url"] for i in page["items"]] == [urls[0], urls[4]]  # newest capture first
    assert page["items"][0]["status_at_capture"] == "open"  # the newest copy of the address
    assert page["items"][1]["location"] == "Town 4" and page["items"][1]["requisition"] == "R-1001"
    rest = (await _get(plane, path, params={"kind": "posting", "offset": "4", "limit": "2"})).json()
    assert [i["url"] for i in rest["items"]] == [urls[1]] and rest["next_offset"] is None
    everything = (await _get(plane, path)).json()
    assert everything["total"] == 6 and {i["kind"] for i in everything["items"]} == {
        "posting",
        "page",
    }
    assert (await _get(plane, path, params={"limit": "201"})).status_code == 422
    assert (await _get(plane, path, params={"kind": "nonsense"})).json()["total"] == 0
    # another tenant's account of the same domain shape sees nothing of it, and the other way
    stranger = await _get(_plane_for(other, store), path)
    assert stranger.status_code == 404
    mine = (
        await _get(_plane_for(other, store), f"/v1/accounts/{other.domain}/archive/list")
    ).json()
    assert [i["url"] for i in mine["items"]] == ["https://jobs.other-tenant.example/x"]
    unknown = await _get(plane, f"/v1/accounts/{uuid4().hex}.example/archive/list")
    assert unknown.status_code == 404


async def test_the_structured_record_of_an_address_wins_over_a_newer_page_copy() -> None:
    """E2E 2026-10-29, D-2: a posting's address has a ``page`` copy (the read text) and a
    ``posting`` record (status, location). "All" and the archived link opened the page copy
    because it was captured a moment later; the richer record now wins, for the index, the
    lookup and the viewer."""
    w = make_world()
    store = MemoryPayloadStore()
    url = "https://jobs.synthetic-acme.example/p/1"
    await _archive(w, store, T0).put_text(url, "posting", "posting record text", title="Admin")
    await _archive(w, store, T0 + timedelta(minutes=1)).put_text(url, "page", "page text")
    async with tenant_txn(w.tenant) as s:
        found = (await LA.lookup(s, w.account, [url], kinds=LA.CLIENT_KINDS))[url]
        listed, total = await LA.list_latest(
            s, w.account, kind=None, offset=0, limit=50, kinds=LA.CLIENT_KINDS
        )
        only_pages, _ = await LA.list_latest(
            s, w.account, kind="page", offset=0, limit=50, kinds=LA.CLIENT_KINDS
        )
    assert found.kind == "posting" and total == 1
    assert [(x.url, x.kind) for x in listed] == [(url, "posting")]
    assert [x.kind for x in only_pages] == ["page"]  # the page filter still lists the page copy
    # with no structured record the page copy is the copy
    other = "https://synthetic-acme.example/news/a"
    await _archive(w, store, T0).put_text(other, "page", "news text")
    async with tenant_txn(w.tenant) as s:
        assert (await LA.lookup(s, w.account, [other]))[other].kind == "page"
